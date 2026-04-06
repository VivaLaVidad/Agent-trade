"""supply_chain — 全球工业品撮合引擎模块自动注册"""

import modules.supply_chain.models  # noqa: F401  确保 ORM 表注册到 Base.metadata

from modules.supply_chain.supply_agent import SupplyAgent
from modules.supply_chain.demand_agent import DemandAgent
from modules.supply_chain.negotiator import NegotiatorAgent
from modules.supply_chain.fx_service import FxRateService
from modules.supply_chain.matching_graph import MatchingOrchestrator


def register(registry) -> None:
    registry.register("supply_agent", SupplyAgent)
    registry.register("demand_agent", DemandAgent)
    registry.register("negotiator", NegotiatorAgent)
    registry.register("fx_service", FxRateService)
    registry.register("matching_orchestrator", MatchingOrchestrator)
