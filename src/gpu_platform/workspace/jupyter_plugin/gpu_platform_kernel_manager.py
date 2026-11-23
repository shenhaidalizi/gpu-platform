"""Loaded inside the Jupyter server's own Python environment."""
from jupyter_server.services.kernels.kernelmanager import AsyncMappingKernelManager


class IdleKernelManager(AsyncMappingKernelManager):
    async def cull_kernel_if_idle(self, kernel_id):
        # A local kernel can already execute a cell while the status subscriber
        # is still connecting. Only an explicit idle status permits idle culling.
        state = getattr(self.get_kernel(kernel_id), "execution_state", None)
        if state in ("idle", "dead"):
            await super().cull_kernel_if_idle(kernel_id)
