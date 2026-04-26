from google.adk.agents.sequential_agent import SequentialAgent
from ..error_diagnosis_agent.agent import root_agent as diagnostic_agent
from ..script_fixer_agent.agent import root_agent as script_fixer_agent

root_agent = SequentialAgent(
    name="eda_debug_pipeline",
    description=(
        "Runs combined Tcl/log diagnosis, then script fixing. "
        "The former analyzer stage is consolidated into the diagnostic agent "
        "to reduce ADK handoff latency."
    ),
    sub_agents=[
        diagnostic_agent,
        script_fixer_agent,
    ],
)