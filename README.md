# 🚀 Intelligent Resume Builder (Powered by Daytona & RAG)

> **Stop sending resumes into the void.** Build ATS-optimized, personally tailored resumes with the power of AI, RAG, and secure isolated environments.

## 🧐 The Problem
**Who uses this?** Job seekers who are tired of "spray and pray" applications.
**Why?**
-   **The ATS Black Box**: 75% of resumes are rejected by Applicant Tracking Systems before a human sees them.
-   **Context Switching**: Tailoring a resume requires digging through years of old docs, performance reviews, and project notes.
-   **Generic Advice**: "Add more metrics" is useless without knowing *which* metrics matter for *this* specific job.

## 💡 The Solution
This **Intelligent Resume Builder** turns your career history into a structured data asset.
1.  **ATS Analysis**: Real-time feedback on keyword gaps and formatting issues.
2.  **RAG (Retrieval-Augmented Generation)**: An AI agent that "remembers" your entire career history and suggests specific, quantifiable bullet points to fill those gaps.
3.  **Secure Architecture**: Uses **Daytona Sandboxes** to isolate file processing and AI workloads, ensuring security and scalability.

---

## 🏗️ Architecture & MCP/RAG Flow

The application follows a distributed, secure architecture designed for the Daytona platform:

```mermaid
graph TD
    User[User Browser] -->|HTTP| Flask[Flask App (Main Sandbox)]
    Flask -->|Manage| DB[Chroma Vector DB]
    Flask -->|Manage| UserFiles[File System]
    
    subgraph "Intelligence Layer (RAG)"
        Flask -->|Query| RAG[RAG Agent (LangGraph)]
        RAG -->|Retrieve| DB
        RAG -->|Connect| MCP[MCP Servers]
        MCP -->|Provide Context| Docs[Document Server]
        MCP -->|Provide Context| ATS_Rules[ATS Rules Server]
    end
    
    subgraph "Compute Layer (Daytona)"
        Flask -->|Create| Worker[Worker Sandbox]
        Worker -->|Execute| Parsing[Resume Parsing]
        Worker -->|Execute| PDF[PDF Generation]
        Worker -->|Execute| ATS[AI Analysis]
    end
```

### Core Components
1.  **Orchestrator (Flask App - `app.py`)**: The main entry point. Handles the UI, user sessions, and coordinates tasks.
2.  **Knowledge Base (`knowledge_base.py`)**: Uses `LangChain` and `ChromaDB` to index user documents (PDFs, text notes). It provides the "long-term memory" for the agent.
3.  **RAG Agent (`rag_agent.py`)**: A LangGraph-based agent that:
    -   Receives a query (e.g., "Find leadership experience").
    -   **Retrieves** relevant chunks from the Knowledge Base.
    -   **Generates** tailored bullet points using a "Detective Retrieval Prompt" to find implicit evidence.
4.  **MCP Servers (`mcp_servers.py`)**: Implements the Model Context Protocol to standardize how the AI accesses data.
    -   `doc_server`: Serves raw document content.
    -   `ats_server`: Provides static ATS best practices and rules.
5.  **Daytona Orchestrator (`daytona_orchestrator.py`)**: Manages ephemeral **Worker Sandboxes**. Heavy tasks like PDF generation and file parsing run here to prevent blocking the main server and ensure process isolation.

---

## ✨ Feature Walkthrough

### 1. The Dashboard & Editor
-   **Split-Screen View**: Edit YAML/Markdown on the left, see a real-time PDF preview on the right.
-   **Live Updates**: Changes are reflected instantly (via `generatePDF` worker).

### 2. ATS Optimization
-   **Paste & Analyze**: Paste a Job Description (JD). The system compares your resume against it.
-   **Visual Scoring**: See an overall match score (0-100) and breakdown by Skills, Experience, and Formatting.
-   **Missing Keywords**: The system highlights high-priority keywords missing from your resume (e.g., "Python", "Agile").

### 3. RAG-Powered Suggestions
-   **"Fill the Gap"**: If you are missing a keyword (e.g., "Leadership"), the **RAG Agent** searches your uploaded performance reviews and old docs.
-   **Evidence Extraction**: It suggests specific bullet points like: *"Led a team of 5 engineers to deliver Project X..."* (sourced from `review_2022.pdf`).
-   **One-Click Add**: Click "Use" to insert the suggestion directly into your resume.

### 4. System Health
-   **Real-time Monitoring**: A status badge in the footer shows the health of the Knowledge Base, Daytona Workers, and MCP Servers.

---

## 🚀 Getting Started

### Prerequisites
-   Python 3.10+
-   **Daytona** (for sandbox orchestration)
-   **Ollama** (optional, for local LLM inference) or an API Key (OpenAI/Gemini).

### Installation

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/yourusername/resume-builder.git
    cd resume-builder
    ```

2.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Run the Application**:
    ```bash
    python app.py
    ```
    *Access the dashboard at `http://localhost:5001`*

### Environment Variables
Create a `.env` file (optional):
```env
ATS_MODEL=ollama/llama3
ATS_API_KEY=your_key_here  # If using Cloud LLMs
DAYTONA_API_KEY=your_key   # Required for worker sandboxes
```

---

## ⚠️ Limitations & Future Work

### Current Limitations
-   **Local-First**: Currently designed for single-user local deployment or Daytona workspaces. Multi-tenant auth is basic.
-   **PDF Generation**: Relies on `wkhtmltopdf` or similar libraries which can be finicky with complex CSS.
-   **Context Window**: Very large knowledge bases might hit LLM context limits during RAG synthesis.

### Future Roadmap
-   **Cloud Sync**: Sync user data to S3/GCS.
-   **Multi-Agent Mode**: Separate agents for "Formatting", "Content", and "Tone" negotiation.
-   **Browser Extension**: Capture job descriptions directly from LinkedIn/Indeed (Prototype in `chrome_extension/`).
-   **Full MCP Support**: Expose the Resume Builder itself as an MCP server for other AI assistants to use.

---

## 🛠️ Tech Stack
-   **Frontend**: HTML5, Tailwind CSS, JavaScript
-   **Backend**: Flask, LangChain, LangGraph
-   **Database**: ChromaDB (Vector Store)
-   **Infrastructure**: Daytona (Dev Environment & Worker Sandboxes)
-   **AI/ML**: Ollama, OpenAI/Gemini, HuggingFace Embeddings
