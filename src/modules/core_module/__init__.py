"""core_module — 外贸核心业务暗箱模块自动注册"""

from modules.core_module.lead_miner import LeadMiner
from modules.core_module.email_campaigner import EmailCampaigner
from modules.core_module.doc_generator import DocGenerator


def register(registry) -> None:
    registry.register("lead_miner", LeadMiner)
    registry.register("email_campaigner", EmailCampaigner)
    registry.register("doc_generator", DocGenerator)
