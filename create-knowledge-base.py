"""
Stage 3: Build a Foundry-ready knowledge base from the HR documents.

What this script does
---------------------
1. Uploads every file in `data/hr-kb/` to a blob container in the configured
   Azure Storage Account.
2. Creates an Azure AI Search index with **integrated vectorization** that
   chunks the documents and embeds them using an Azure OpenAI embedding
   deployment.
3. Creates a data source (pointing to the blob container) and an indexer
   that crawls the container and populates the index.
4. Runs the indexer once.

The resulting index can be added as a **Knowledge** source to any Foundry
agent (portal: Agent -> Knowledge -> "+ Azure AI Search", or via the
`AzureAISearchTool` in the Agent Framework).

Auth
----
Everything uses `DefaultAzureCredential` (works with `azd auth login` /
`az login`). No keys are required.

Make sure the identity running this script has:
    - Storage Blob Data Contributor on the storage account
    - Search Service Contributor + Search Index Data Contributor on the
      AI Search service
    - Cognitive Services OpenAI User on the Azure OpenAI resource

And that the AI Search service's managed identity has:
    - Storage Blob Data Reader on the storage account
    - Cognitive Services OpenAI User on the Azure OpenAI resource

Run:
    uv run 3-create-knowledge-base.py
"""

import logging
import os
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient, SearchIndexerClient
from azure.search.documents.indexes.models import (
    AzureOpenAIEmbeddingSkill,
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
    FieldMapping,
    HnswAlgorithmConfiguration,
    IndexProjectionMode,
    InputFieldMappingEntry,
    KnowledgeBase,
    KnowledgeBaseAzureOpenAIModel,
    KnowledgeSourceReference,
    OutputFieldMappingEntry,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchIndexKnowledgeSource,
    SearchIndexKnowledgeSourceParameters,
    SearchIndexer,
    SearchIndexerDataContainer,
    SearchIndexerDataSourceConnection,
    SearchIndexerIndexProjection,
    SearchIndexerIndexProjectionSelector,
    SearchIndexerIndexProjectionsParameters,
    SearchIndexerSkillset,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    SplitSkill,
    VectorSearch,
    VectorSearchProfile,
)
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler

load_dotenv(override=True)

console = Console()
logger = logging.getLogger("kb")

DATA_DIR = Path(__file__).parent / "data" / "hr-kb"


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing env var: {name}")
    return value


# ---------- Step 1: upload files to blob ----------

def upload_files(credential: DefaultAzureCredential) -> None:
    account = env("AZURE_STORAGE_ACCOUNT_NAME")
    container = env("AZURE_STORAGE_CONTAINER_NAME")

    blob_service = BlobServiceClient(
        account_url=f"https://{account}.blob.core.windows.net",
        credential=credential,
    )

    container_client = blob_service.get_container_client(container)
    if not container_client.exists():
        console.print(f"Creating container [bold]{container}[/bold]")
        container_client.create_container()

    for path in sorted(DATA_DIR.iterdir()):
        if not path.is_file():
            continue
        console.print(f"  uploading {path.name}")
        with path.open("rb") as f:
            container_client.upload_blob(name=path.name, data=f, overwrite=True)


# ---------- Step 2 + 3: Search index, data source, skillset, indexer ----------

def build_knowledge_base(credential: DefaultAzureCredential) -> None:
    search_endpoint = env("AZURE_SEARCH_ENDPOINT")
    index_name = env("AZURE_SEARCH_INDEX_NAME")
    aoai_endpoint = env("AZURE_OPENAI_ENDPOINT")
    embedding_deployment = env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME")
    embedding_model = env("AZURE_OPENAI_EMBEDDING_MODEL")
    embedding_dims = int(env("AZURE_OPENAI_EMBEDDING_DIMENSIONS"))

    subscription_id = env("AZURE_SUBSCRIPTION_ID")
    resource_group = env("AZURE_RESOURCE_GROUP")
    storage_account = env("AZURE_STORAGE_ACCOUNT_NAME")
    container = env("AZURE_STORAGE_CONTAINER_NAME")

    storage_resource_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Storage/storageAccounts/{storage_account}"
    )

    index_client = SearchIndexClient(endpoint=search_endpoint, credential=credential)
    indexer_client = SearchIndexerClient(endpoint=search_endpoint, credential=credential)

    # ----- Index (parent = chunks) -----
    vector_profile = "hnsw-aoai"
    vectorizer_name = "aoai-vectorizer"

    index = SearchIndex(
        name=index_name,
        fields=[
            SearchableField(name="chunk_id", type=SearchFieldDataType.String, key=True,
                            sortable=True, filterable=True, facetable=True,
                            analyzer_name="keyword"),
            SimpleField(name="parent_id", type=SearchFieldDataType.String,
                        filterable=True),
            SearchableField(name="title", type=SearchFieldDataType.String),
            SearchableField(name="chunk", type=SearchFieldDataType.String),
            SearchField(
                name="embedding",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                vector_search_dimensions=embedding_dims,
                vector_search_profile_name=vector_profile,
            ),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
            profiles=[
                VectorSearchProfile(
                    name=vector_profile,
                    algorithm_configuration_name="hnsw",
                    vectorizer_name=vectorizer_name,
                )
            ],
            vectorizers=[
                AzureOpenAIVectorizer(
                    vectorizer_name=vectorizer_name,
                    parameters=AzureOpenAIVectorizerParameters(
                        resource_url=aoai_endpoint,
                        deployment_name=embedding_deployment,
                        model_name=embedding_model,
                    ),
                )
            ],
        ),
        semantic_search=SemanticSearch(
            default_configuration_name="default",
            configurations=[
                SemanticConfiguration(
                    name="default",
                    prioritized_fields=SemanticPrioritizedFields(
                        title_field=SemanticField(field_name="title"),
                        content_fields=[SemanticField(field_name="chunk")],
                    ),
                )
            ],
        ),
    )
    console.print(f"Creating/updating index [bold]{index_name}[/bold]")
    index_client.create_or_update_index(index)

    # ----- Data source (blob, managed identity via ResourceId) -----
    data_source_name = f"{index_name}-blob-ds"
    data_source = SearchIndexerDataSourceConnection(
        name=data_source_name,
        type="azureblob",
        connection_string=f"ResourceId={storage_resource_id};",
        container=SearchIndexerDataContainer(name=container),
    )
    console.print(f"Creating/updating data source [bold]{data_source_name}[/bold]")
    indexer_client.create_or_update_data_source_connection(data_source)

    # ----- Skillset: split -> embed -> project to index -----
    skillset_name = f"{index_name}-skillset"
    split_skill = SplitSkill(
        description="Split into chunks",
        text_split_mode="pages",
        context="/document",
        maximum_page_length=2000,
        page_overlap_length=200,
        inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
        outputs=[OutputFieldMappingEntry(name="textItems", target_name="pages")],
    )
    embed_skill = AzureOpenAIEmbeddingSkill(
        description="Embed each chunk",
        context="/document/pages/*",
        resource_url=aoai_endpoint,
        deployment_name=embedding_deployment,
        model_name=embedding_model,
        dimensions=embedding_dims,
        inputs=[InputFieldMappingEntry(name="text", source="/document/pages/*")],
        outputs=[OutputFieldMappingEntry(name="embedding", target_name="embedding")],
    )
    index_projection = SearchIndexerIndexProjection(
        selectors=[
            SearchIndexerIndexProjectionSelector(
                target_index_name=index_name,
                parent_key_field_name="parent_id",
                source_context="/document/pages/*",
                mappings=[
                    InputFieldMappingEntry(name="chunk", source="/document/pages/*"),
                    InputFieldMappingEntry(name="embedding",
                                           source="/document/pages/*/embedding"),
                    InputFieldMappingEntry(name="title",
                                           source="/document/metadata_storage_name"),
                ],
            )
        ],
        parameters=SearchIndexerIndexProjectionsParameters(
            projection_mode=IndexProjectionMode.SKIP_INDEXING_PARENT_DOCUMENTS
        ),
    )
    skillset = SearchIndexerSkillset(
        name=skillset_name,
        skills=[split_skill, embed_skill],
        index_projection=index_projection,
    )
    console.print(f"Creating/updating skillset [bold]{skillset_name}[/bold]")
    indexer_client.create_or_update_skillset(skillset)

    # ----- Indexer -----
    indexer_name = f"{index_name}-indexer"
    indexer = SearchIndexer(
        name=indexer_name,
        data_source_name=data_source_name,
        target_index_name=index_name,
        skillset_name=skillset_name,
        field_mappings=[
            FieldMapping(source_field_name="metadata_storage_name", target_field_name="title"),
        ],
    )
    console.print(f"Creating/updating indexer [bold]{indexer_name}[/bold]")
    indexer_client.create_or_update_indexer(indexer)

    console.print(f"Running indexer [bold]{indexer_name}[/bold]")
    indexer_client.run_indexer(indexer_name)
    console.print(
        "[green]Indexer started.[/green] Check status in the Azure portal "
        "(Search service -> Indexers)."
    )


# ---------- Step 4: Foundry IQ knowledge source + knowledge base ----------

def build_foundry_iq_knowledge_base(credential: DefaultAzureCredential) -> None:
    """Create a Foundry IQ knowledge source + knowledge base on top of the index.

    The MCP endpoint at /knowledgebases/<name>/mcp only exists for an actual
    KnowledgeBase resource; pointing it at a bare search index returns 404
    (which the MCP client surfaces as 'Session terminated').
    """
    search_endpoint = env("AZURE_SEARCH_ENDPOINT")
    index_name = env("AZURE_SEARCH_INDEX_NAME")
    kb_name = env("AZURE_SEARCH_KB_NAME")
    ks_name = f"{kb_name}-ks"

    aoai_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    chat_deployment = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    chat_model = os.environ.get("AZURE_OPENAI_CHAT_MODEL")

    index_client = SearchIndexClient(endpoint=search_endpoint, credential=credential)

    # Knowledge source: read from the search index we just built.
    knowledge_source = SearchIndexKnowledgeSource(
        name=ks_name,
        description=f"Knowledge source over the {index_name} index",
        search_index_parameters=SearchIndexKnowledgeSourceParameters(
            search_index_name=index_name,
            semantic_configuration_name="default",
        ),
    )
    console.print(f"Creating/updating knowledge source [bold]{ks_name}[/bold]")
    index_client.create_or_update_knowledge_source(knowledge_source)

    # Knowledge base: references the knowledge source. Optionally include an
    # Azure OpenAI chat model for query planning + answer synthesis (preview).
    models = None
    if aoai_endpoint and chat_deployment and chat_model:
        models = [
            KnowledgeBaseAzureOpenAIModel(
                azure_open_ai_parameters=AzureOpenAIVectorizerParameters(
                    resource_url=aoai_endpoint,
                    deployment_name=chat_deployment,
                    model_name=chat_model,
                ),
            )
        ]

    knowledge_base = KnowledgeBase(
        name=kb_name,
        description="HR knowledge base for Foundry IQ",
        knowledge_sources=[KnowledgeSourceReference(name=ks_name)],
        models=models,
    )
    console.print(f"Creating/updating knowledge base [bold]{kb_name}[/bold]")
    index_client.create_or_update_knowledge_base(knowledge_base)
    console.print(
        f"[green]Knowledge base ready.[/green] MCP endpoint: "
        f"{search_endpoint}/knowledgebases/{kb_name}/mcp?api-version=2025-11-01-Preview"
    )


def main():
    credential = DefaultAzureCredential()

    console.rule("[bold]1. Uploading files to blob[/bold]")
    upload_files(credential)

    console.rule("[bold]2. Building Azure AI Search index + indexer[/bold]")
    build_knowledge_base(credential)

    console.rule("[bold]3. Building Foundry IQ knowledge base[/bold]")
    build_foundry_iq_knowledge_base(credential)

    console.rule("[bold green]Done[/bold green]")
    console.print(
        "Connect this knowledge base to your Foundry agent via "
        "[bold]Knowledge -> + Azure AI Search[/bold] in the Foundry portal, "
        "or via the MCP endpoint shown above."
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        handlers=[RichHandler(console=console, show_path=False)])
    logging.getLogger("azure").setLevel(logging.WARNING)
    main()
