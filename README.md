Autonomous Multi-Agent FinOps & Cloud Infrastructure OptimizerAn enterprise-grade, autonomous multi-agent system designed to optimize cloud infrastructure spend. Leveraging LangGraph, Pydantic guardrails, and CDKTF, the platform continuously monitors workloads for underutilization, orchestrates safe downsizing workflows, and programmatically applies infrastructure modifications.🏗️ Architecture & Component DesignThe platform operates via a decoupled, event-driven, and agentic architecture split into seven key functional areas:[ Telemetry Generator ] ──> [ Ingestion Layer ] ──> [ TimescaleDB / SQLite ]
                                                              │
[ Streamlit UI ] <── [ Agent Orchestration (LangGraph) ] <────┘
                          ├── FinOps Analyst Agent
                          └── DevOps Execution Agent ──> [ Guardrails ] ──> [ CDKTF Deploy ]
Telemetry Generation Layer: Simulates real-world production microservices by emitting granular, real-time system metrics (CPU utilization, memory allocation, and hourly cost signatures).Data Ingestion Engine: Handles streaming telemetry data with production-ready ingestion into PostgreSQL / TimescaleDB, featuring a seamless zero-configuration SQLite local fallback.FinOps Analyst Agent (LangGraph): Orchestrates scheduled data-mining routines over historical telemetry windows to flag structurally underutilized infrastructure (e.g., sustained $CPU < 15\%$).DevOps Reasoning Agent (LangGraph): Evaluates candidate services for optimization, generates contextual engineering justifications, and proposes deterministic right-sizing paths.Deterministic Safety Guardrails: Implements strict validation policies utilizing Pydantic. Enforces resource-level cool-down locks (e.g., limiting modifications to a maximum of 1 resize per 4-hour window) and validates SKU mutations against hardcoded organizational allow-lists.Infrastructure as Code (IaC) Synthesis: Dynamically compiles localized Cloud Development Kit for Terraform (CDKTF) stacks, runs automated dry-run verifications (cdktf plan), and safely applies approved cloud state updates.Executive Streamlit Dashboard: Provides deep observability into realized cost-savings, live aggregate fleet expenditures, and historical agent reasoning audit trails.🛠️ Local Development & DeploymentThe system is designed with a zero-dependency architecture fallback. If dedicated PostgreSQL or Redpanda instances are omitted, the runtime gracefully defaults to internal SQLite and Mock Kafka implementations.PrerequisitesPython 3.10 or higherNode.js & npm (Required for CDKTF core compilation)1. Installation & Environment SetupClone the repository and initialize the isolated virtual environment:Bashgit clone https://github.com/karuna0733/Finops-optimizer.git
cd Finops-optimizer/finops-optimizer

# Initialize and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
2. Executing the Telemetry PipelinesTo simulate cloud infrastructure workloads, spin up the telemetry generation and consumer loops in isolated shell sessions:Session A (Metric Generation Pipeline):Bashpython telemetry/telemetry_generator.py
Session B (Stream Consumer & Storage Ingestion):Bashpython telemetry/telemetry_consumer.py
3. Triggering the Agentic Reasoning LoopExecute the core LangGraph state machine manually to analyze metrics, parse safety thresholds, and generate active resizing strategies:Bashpython agents/agent_graph.py
4. Launching the Management DashboardTo visually monitor infrastructure health, trace agent decisions, and manually force optimization cycles, launch the Streamlit frontend UI:Bashstreamlit run dashboard/dashboard.py
