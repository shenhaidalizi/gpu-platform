import asyncio
import logging
from uuid import uuid4
from time import monotonic

import httpx
from fastapi import HTTPException

from gpu_platform.scheduler.profile import Observation, estimate
from gpu_platform.workspace.fingerprint import script_command
from gpu_platform.sharing import accessible, can_fit, shareable_mib

log = logging.getLogger(__name__)
ACTIVE = "state IN ('starting','running','stopping')"


class Engine:
    def __init__(self, settings, store, runner):
        self.settings = settings
        self.store = store
        self.runner = runner
        self.owners = {}
        self.inventory = []
        self.host_gpu_sample = {"sampled": None, "cards": []}
        self.pending = {}
        self.observations = {}
        self.wake = asyncio.Event()
        self.ready = False
        self.problem = None
        self.loops = []

    def start(self):
        self.loops = [asyncio.create_task(self._loop()), asyncio.create_task(self._events()),
                      asyncio.create_task(self._collect_usage())]

    async def close(self):
        for loop in self.loops:
            loop.cancel()
        await asyncio.gather(*self.loops, return_exceptions=True)
        # Finish outstanding start acknowledgements. Task units live independently.
        await asyncio.gather(*list(self.pending.values()), return_exceptions=True)

    async def get_owned(self, ident, user):
        rows = await self.store.rows("id=? AND user=?", (ident, user))
        if not rows:
            raise HTTPException(404, "任务不存在")
        return rows[0]

    async def submit(self, user, spec):
        if not self.ready:
            raise HTTPException(503, "执行器正在恢复，暂缓提交")
        if spec.gpu_count > len(self.settings.gpu_uuids):
            raise HTTPException(422, "申请数量超过纳管 GPU 总数")
        task = await self.store.create(user, spec)
        self.wake.set()
        return task

    async def cancel(self, ident, user):
        await self.get_owned(ident, user)
        await self.store.cancel(ident)
        self.wake.set()
        return await self.get_owned(ident, user)

    def _release(self, run):
        self.observations.pop(run, None)
        for gpu, holders in list(self.owners.items()):
            holders.pop(run, None)
            if not holders:
                del self.owners[gpu]

    def gpu_info(self, card):
        holders = self.owners.get(card["uuid"], {})
        runs = list(holders)
        return {**card, "available": can_fit(card, holders, None, self.settings.shared_limit,
                                             self.settings.shared_headroom_mib),
                "run_ids": runs, "run_id": next(iter(runs), None),
                "shared_reserved_mib": sum(b or 0 for b in holders.values()),
                "shared_limit": self.settings.shared_limit,
                "shared_headroom_mib": self.settings.shared_headroom_mib,
                "shared_free_mib": shareable_mib(card, holders, self.settings.shared_limit,
                                                 self.settings.shared_headroom_mib)}

    async def _collect_usage(self):
        previous_error = None
        while True:
            try:
                frame = await self.runner.call("GET", "/usage")
                self.host_gpu_sample = {"sampled": frame["sampled"], "cards": frame["cards"]}
                await self.store.record_usage(frame)
                previous_error = None
            except Exception as error:
                if str(error) != previous_error:
                    log.warning("Host GPU telemetry unavailable: %s", error)
                    previous_error = str(error)
            await asyncio.sleep(5)

    async def _events(self):
        seq = 0
        while True:
            try:
                event = await self.runner.call("GET", "/events", params={"after": seq})
                if event["seq"] != seq:
                    self.wake.set()
                seq = event["seq"]
            except (httpx.HTTPError, OSError):
                await asyncio.sleep(1)

    def _fits(self, card, budget):
        return can_fit(card, self.owners.get(card["uuid"], {}), budget,
                       self.settings.shared_limit, self.settings.shared_headroom_mib)

    async def _profile_key(self, task):
        if task["kind"] != "batch" or task["gpu_count"] != 1 or not script_command(task["argv"]):
            return None
        try:
            result = await self.runner.call("POST", "/workspace/profile",
                                           json={k: task[k] for k in ("user", "cwd", "argv")})
            if result.get("legacy") and result.get("key"):
                await self.store.promote_profile(result["legacy"], result["key"])
            return result.get("key")
        except (httpx.HTTPError, OSError):
            return None

    async def _observe(self, task):
        run = task["run_id"]
        state = await self.runner.call("GET", f"/runs/{run}")
        status = state["state"]
        if task["state"] == "stopping" and status == "running":
            await self.runner.call("POST", f"/runs/{run}/stop")
            return
        if status == "running" and task["state"] == "starting":
            await self.store.started(run)
            return
        if status == "missing" and task["state"] == "starting" and not task["dispatched"]:
            await self.store.unreserve(run)
            self._release(run)
            return
        if status in ("succeeded", "failed", "missing") and state["empty"]:
            # A missing unit after an interrupted start has an unknown outcome.
            # Never reexecute a potentially completed training command.
            result = "succeeded" if status == "succeeded" else "failed"
            message = state.get("error") or ("运行实例缺失，退出结果未知" if status == "missing" else None)
            sample = None
            observation = self.observations.get(run)
            if result == "succeeded" and task["state"] != "stopping" and observation:
                sample = observation.baseline()
                if sample and await self._profile_key(task) != task["profile_key"]:
                    sample = None
            over_budget = bool(observation and task["gpu_memory_mib"]
                               and observation.peak_mib > task["gpu_memory_mib"])
            await self.store.finish(run, result, message, state.get("exit_code"), sample, over_budget)
            self._release(run)

    async def _launch(self, task, run, gpus):
        try:
            if not await self.store.reserve(task["id"], run, gpus, task["gpu_memory_mib"], task.get("profile_key")):
                self._release(run)
                return
            if not await self.store.dispatch(run):
                await self.store.finish(run, "cancelled")
                self._release(run)
                return
            payload = {key: task[key] for key in ("user", "kind", "cwd", "argv", "gpu_count", "gpu_memory_mib")}
            await self.runner.call("POST", f"/runs/{run}/start", json={**payload, "gpus": gpus, "gpu_on_demand": bool(task["gpu_on_demand"])})
            await self.store.started(run)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                await self.store.unreserve(run)
                self._release(run)
            elif 400 <= exc.response.status_code < 500:
                await self.store.finish(run, "failed", exc.response.text[:1000])
                self._release(run)
            else:
                log.warning("Runner start outcome unknown for %s: %s", run, exc)
        except Exception:
            # Keep capacity reserved until a fresh runner observation resolves it.
            log.exception("Start outcome unknown for %s", run)
            self.ready = False
        finally:
            self.pending.pop(task["id"], None)
            self.wake.set()

    async def _tick(self):
        active = await self.store.rows(ACTIVE)
        if not self.ready:
            # Wait for concurrent starts before rebuilding the in-memory snapshot.
            if self.pending:
                return
            await self.runner.call("GET", "/gpus")
            await self.store.rebuild_allocations(active)
            self.owners = {}
            self.observations.clear()
            for task in active:
                if task["gpu_on_demand"]:
                    continue
                for gpu in task["gpus"]:
                    self.owners.setdefault(gpu, {})[task["run_id"]] = task["gpu_memory_mib"]
        for task in active:
            if task["id"] not in self.pending:
                await self._observe(task)
        # start requests may still be committing. Never classify those units as foreign.
        runs = {row["run_id"] for row in await self.store.rows(ACTIVE)}
        runs.update(run for holders in self.owners.values() for run in holders)
        self.inventory = await self.runner.call("POST", "/reconcile", json={"runs": sorted(runs)})
        self.ready, self.problem = True, None
        cards = {g["uuid"]: g for g in self.inventory}
        for gpu, holders in self.owners.items():
            for run in holders:
                if run in self.observations:
                    self.observations[run].observe(run, cards.get(gpu), holders, monotonic())
        blocked = False
        for task in await self.store.rows("state='queued'"):
            if task["id"] in self.pending:
                continue
            count = task["gpu_count"]
            budget = None
            if task["gpu_on_demand"]:
                selected = [uuid for uuid in self.settings.gpu_uuids
                            if uuid in cards and accessible(cards[uuid])][:count]
                if len(selected) < count:
                    continue
            else:
                if count and blocked:
                    continue
                # Avoid rereading a queued script while every suitable card is fully booked.
                candidates = [uuid for uuid in self.settings.gpu_uuids if uuid in cards
                              and (self._fits(cards[uuid], None) or (count == 1 and self._fits(cards[uuid], 1)))]
                if len(candidates) < count:
                    blocked = True
                    continue
                key = await self._profile_key(task)
                profiles = await self.store.profiles(key) if key else {}
                task["profile_key"] = key
                selected = []
                for uuid in candidates if count else ():
                    card = cards[uuid]
                    holders = self.owners.get(uuid, {})
                    candidate = estimate(profiles.get(uuid), self.settings.shared_limit)
                    headroom = self.settings.shared_headroom_mib
                    if (candidate and not holders and not card.get("jupyter_using_gpu")
                            and candidate > shareable_mib(card, holders, self.settings.shared_limit, headroom)):
                        candidate = None
                    if self._fits(card, candidate):
                        budget = candidate
                        selected.append(uuid)
                        if len(selected) == count:
                            break
                if len(selected) < count:
                    blocked = True
                    continue
            task["gpu_memory_mib"] = budget
            run = uuid4().hex
            if not task["gpu_on_demand"]:
                for gpu in selected:
                    holders = self.owners.setdefault(gpu, {})
                    if task.get("profile_key"):
                        self.observations[run] = Observation(solo=not holders)
                    for other in holders:
                        if other in self.observations:
                            self.observations[other].solo = False
                    holders[run] = budget
            self.pending[task["id"]] = asyncio.create_task(self._launch(task, run, selected))

    async def _loop(self):
        while True:
            self.wake.clear()
            try:
                await self._tick()
            except Exception as exc:
                self.ready = False
                if self.problem != str(exc):
                    log.warning("Dispatch suspended: %s", exc)
                self.problem = str(exc)
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
