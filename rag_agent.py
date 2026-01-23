import json
import os
from typing import Annotated, Dict, List, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from litellm import completion

from knowledge_base import KnowledgeBase


# State Definition
class AgentState(TypedDict):
    query: str
    context: List[str]
    answer: str
    suggestions: List[str]
    model: str
    api_key: str


class RagAgent:
    def __init__(self):
        self.kb = KnowledgeBase()
        self.default_model = os.getenv("ATS_MODEL", "ollama/llama3")
        self.default_api_key = os.getenv("ATS_API_KEY")

        # Define Graph
        workflow = StateGraph(AgentState)

        # Add Nodes
        workflow.add_node("retrieve", self.retrieve)
        workflow.add_node("generate_bullets", self.generate_bullets)
        workflow.add_node("interview_coach", self.interview_coach)

        # Set Entry Point (Dynamic based on intent, but for now we'll have methods wrapping the graph)
        # We will expose specific methods instead of a single graph entry for simplicity in this turn
        self.workflow = workflow

    def retrieve(self, state: AgentState):
        """Node: Retrieve documents from Knowledge Base."""
        print(f"Retrieving for: {state['query']}")
        docs = self.kb.search(state["query"])
        context = [doc.page_content for doc in docs]
        return {"context": context}

    def generate_bullets(self, state: AgentState):
        """Node: Generate resume bullet points based on context using Detective Retrieval Prompt."""
        context_str = "\n\n".join(state["context"])
        prompt = f"""
        <System_Instruction> 
        Objective: Extract "Implicit Evidence" from the user's career history. 
        Search_Query: "Find evidence of {state['query']} impact." 
        
        Procedure: 
        1. Search the Achievement Vault (Context provided below) for direct matches to {state['query']}. 
        2. If no direct match is found, perform a "Semantic Pivot": Search for related concepts (e.g., if searching for 'Negotiation', look for 'Vendor Management', 'Conflict Resolution', or 'Cost Savings'). 
        3. For every piece of evidence found, extract: [Project Name], [Specific Metric], and [Action Verb]. 
        4. Output Format: Return a JSON list of "Evidence Blocks" ready for the 'Tailor' tool. 
        Example: ["Project Alpha: Reduced latency by 40% (Optimized API)", "Sales: Generated $50k revenue (New Client Acquisition)"]
        </System_Instruction>
        
        Achievement Vault Context:
        {context_str}
        """

        model = state.get("model") or self.default_model
        api_key = state.get("api_key") or self.default_api_key

        if model.startswith("gemini/") and api_key:
            os.environ["GEMINI_API_KEY"] = api_key
            os.environ["GOOGLE_API_KEY"] = api_key

        try:
            response = completion(model=model, messages=[{"role": "user", "content": prompt}], api_key=api_key)
            content = response.choices[0].message.content
            # Basic cleaning if LLM wraps in markdown
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1]

            return {"suggestions": content.strip()}  # Expecting JSON string
        except Exception as e:
            print(f"LLM Generation Error: {e}")
            return {"suggestions": json.dumps(["Could not generate suggestions. Please check if the LLM is running."])}

    def interview_coach(self, state: AgentState):
        """Node: Answer interview questions based on context."""
        context_str = "\n\n".join(state["context"])
        prompt = f"""
        You are an Interview Coach. Use the user's background information below to answer the interview question 
        or provide talking points.
        
        Context:
        {context_str}
        
        Interview Question: {state['query']}
        
        Output: Provide a structured answer with "Key Talking Points" and a "Sample Answer".
        """

        model = state.get("model") or self.default_model
        api_key = state.get("api_key") or self.default_api_key

        if model.startswith("gemini/") and api_key:
            os.environ["GEMINI_API_KEY"] = api_key
            os.environ["GOOGLE_API_KEY"] = api_key

        try:
            response = completion(model=model, messages=[{"role": "user", "content": prompt}], api_key=api_key)
            return {"answer": response.choices[0].message.content}
        except Exception as e:
            print(f"LLM Interview Error: {e}")
            return {"answer": f"Error generating answer: {str(e)}. Please check your LLM configuration."}

    # Public Methods to run the logic
    def find_lost_bullets(self, query: str, model: str = None, api_key: str = None):
        """Runs the RAG pipeline to find bullet points."""
        # Manually running the sequence for simplicity
        state = {"query": query, "context": [], "answer": "", "suggestions": [], "model": model, "api_key": api_key}
        retrieved = self.retrieve(state)
        state.update(retrieved)
        result = self.generate_bullets(state)
        return result["suggestions"]

    def prep_for_interview(self, question: str, model: str = None, api_key: str = None):
        """Runs the RAG pipeline for interview prep."""
        state = {"query": question, "context": [], "answer": "", "suggestions": [], "model": model, "api_key": api_key}
        retrieved = self.retrieve(state)
        state.update(retrieved)
        result = self.interview_coach(state)
        return result["answer"]
