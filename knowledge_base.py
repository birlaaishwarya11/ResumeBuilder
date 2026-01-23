import os
import shutil
from typing import List

from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader, TextLoader, UnstructuredFileLoader, WebBaseLoader
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Configuration
PERSIST_DIRECTORY = os.path.join("data", "chroma_db")
# Using a local embedding model via Ollama (e.g., nomic-embed-text or llama3)
# Fallback to a simple HuggingFace model if Ollama is not available?
# For now, let's assume the user has 'nomic-embed-text' or uses 'llama3' for embeddings too.
EMBEDDING_MODEL_NAME = "nomic-embed-text"


class KnowledgeBase:
    def __init__(self):
        self.persist_directory = PERSIST_DIRECTORY
        os.makedirs(self.persist_directory, exist_ok=True)

        # Initialize Embeddings
        # We can switch to OpenAIEmbeddings if API key is present
        self.embeddings = OllamaEmbeddings(model=EMBEDDING_MODEL_NAME)

        # Initialize Vector Store
        self.vector_db = Chroma(persist_directory=self.persist_directory, embedding_function=self.embeddings)

    def add_documents(self, file_paths: List[str]):
        """Ingests documents (PDF, Text, etc.) into the vector store."""
        documents = []
        for path in file_paths:
            if not os.path.exists(path):
                continue

            ext = os.path.splitext(path)[1].lower()
            try:
                if ext == ".pdf":
                    loader = PyPDFLoader(path)
                elif ext in [".txt", ".md", ".log"]:
                    loader = TextLoader(path)
                else:
                    loader = UnstructuredFileLoader(path)

                docs = loader.load()
                documents.extend(docs)
            except Exception as e:
                print(f"Error loading {path}: {e}")

        if not documents:
            return "No documents loaded."

        # Split text
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        splits = text_splitter.split_documents(documents)

        # Add to Chroma
        self.vector_db.add_documents(splits)
        self.vector_db.persist()
        return f"Successfully added {len(splits)} chunks to the knowledge base."

    def health_check(self):
        """Checks if the vector database is accessible."""
        try:
            # Perform a lightweight check, e.g., count collection items or a dummy search
            # Chroma's get() is cheap
            self.vector_db.get(limit=1)
            return True
        except Exception as e:
            print(f"KnowledgeBase Health Check Failed: {e}")
            return False

    def add_text(self, text: str, source: str = "manual_input"):
        """Ingests raw text."""
        doc = Document(page_content=text, metadata={"source": source})
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        splits = text_splitter.split_documents([doc])
        self.vector_db.add_documents(splits)
        # self.vector_db.persist() # Chroma 0.4+ persists automatically
        return "Text added to knowledge base."

    def add_web_page(self, url: str):
        """Ingests content from a web page."""
        try:
            loader = WebBaseLoader(url)
            docs = loader.load()

            text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
            splits = text_splitter.split_documents(docs)

            self.vector_db.add_documents(splits)
            # self.vector_db.persist()
            return f"Successfully scraped and added content from {url}"
        except Exception as e:
            return f"Error scraping {url}: {str(e)}"

    def search(self, query: str, k: int = 4):
        """Retrieves relevant documents."""
        return self.vector_db.similarity_search(query, k=k)

    def clear(self):
        """Clears the database."""
        if os.path.exists(self.persist_directory):
            shutil.rmtree(self.persist_directory)
            os.makedirs(self.persist_directory, exist_ok=True)
            self.vector_db = Chroma(persist_directory=self.persist_directory, embedding_function=self.embeddings)
        return "Knowledge base cleared."
