from google.adk.agents.sequential_agent import SequentialAgent
from ..script_analyzer_agent.agent import root_agent as script_analyzer_agent
from ..error_diagnosis_agent.agent import root_agent as error_diagnosis_agent
from ..script_fixer_agent.agent import root_agent as script_fixer_agent

root_agent = SequentialAgent(
    name="eda_debug_pipeline",
    description="Runs script analysis, then error diagnosis, then script fixing in strict sequence.",
    sub_agents=[script_analyzer_agent, error_diagnosis_agent, script_fixer_agent],
)