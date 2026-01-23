import os
import json
from typing import Dict, List, TypedDict, Annotated
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage
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
        docs = self.kb.search(state['query'])
        context = [doc.page_content for doc in docs]
        return {"context": context}

    def generate_bullets(self, state: AgentState):
        """Node: Generate resume bullet points based on context."""
        context_str = "\n\n".join(state['context'])
        prompt = f"""
        You are a Resume Expert. Based on the user's past documents (emails, old resumes) below, 
        reconstruct specific, quantifiable bullet points for their resume.
        
        Context:
        {context_str}
        
        User Query/Focus: {state['query']}
        
        Output: Return ONLY a JSON list of strings. Example: ["Managed a team of 5...", "Increased revenue by 20%..."]
        """
        
        model = state.get('model') or self.default_model
        api_key = state.get('api_key') or self.default_api_key
        
        if model.startswith("gemini/") and api_key:
            os.environ["GEMINI_API_KEY"] = api_key
            os.environ["GOOGLE_API_KEY"] = api_key
        
        try:
            response = completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                api_key=api_key
            )
            content = response.choices[0].message.content
            # Basic cleaning if LLM wraps in markdown
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1]
                
            return {"suggestions": content.strip()} # Expecting JSON string
        except Exception as e:
            print(f"LLM Generation Error: {e}")
            return {"suggestions": json.dumps(["Could not generate suggestions. Please check if the LLM is running."])}

    def interview_coach(self, state: AgentState):
        """Node: Answer interview questions based on context."""
        context_str = "\n\n".join(state['context'])
        prompt = f"""
        You are an Interview Coach. Use the user's background information below to answer the interview question 
        or provide talking points.
        
        Context:
        {context_str}
        
        Interview Question: {state['query']}
        
        Output: Provide a structured answer with "Key Talking Points" and a "Sample Answer".
        """
        
        model = state.get('model') or self.default_model
        api_key = state.get('api_key') or self.default_api_key
        
        if model.startswith("gemini/") and api_key:
            os.environ["GEMINI_API_KEY"] = api_key
            os.environ["GOOGLE_API_KEY"] = api_key
        
        try:
            response = completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                api_key=api_key
            )
            return {"answer": response.choices[0].message.content}
        except Exception as e:
            print(f"LLM Interview Error: {e}")
            return {"answer": f"Error generating answer: {str(e)}. Please check your LLM configuration."}

    # Public Methods to run the logic
    def find_lost_bullets(self, query: str, model: str = None, api_key: str = None):
        """Runs the RAG pipeline to find bullet points."""
        # Manually running the sequence for simplicity
        state = {
            "query": query, 
            "context": [], 
            "answer": "", 
            "suggestions": [],
            "model": model,
            "api_key": api_key
        }
        retrieved = self.retrieve(state)
        state.update(retrieved)
        result = self.generate_bullets(state)
        return result["suggestions"]

    def prep_for_interview(self, question: str, model: str = None, api_key: str = None):
        """Runs the RAG pipeline for interview prep."""
        state = {
            "query": question, 
            "context": [], 
            "answer": "", 
            "suggestions": [],
            "model": model,
            "api_key": api_key
        }
        retrieved = self.retrieve(state)
        state.update(retrieved)
        result = self.interview_coach(state)
        return result["answer"]
