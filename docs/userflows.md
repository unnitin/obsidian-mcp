# User Flows

Seven Mermaid diagrams covering every interaction path in the system.

---

## 1. System Architecture

High-level view of all components and how they connect.

```mermaid
graph LR
    subgraph iCloud ["☁️ Obsidian Vault (iCloud Sync)"]
        Notes["📝 Notes/*.md"]
        PDFs["📄 PDFs"]
        DB[".obsidian-search/<br/>semantic-search.db<br/>(sqlite-vec)"]
        Plugin[".obsidian/plugins/<br/>obsidian-semantic-search/<br/>main.js"]
    end

    subgraph Backend ["🐍 Python Backend (local process)"]
        API["FastAPI<br/>port 51234"]
        MCP_SRV["FastMCP Server<br/>(stdio)"]
        Embedder["nomic-embed-text-v1.5<br/>(sentence-transformers)"]
        Watcher["watchdog<br/>FSEventsObserver"]
    end

    subgraph Clients ["Clients"]
        Obsidian["Obsidian App"]
        Claude["Claude Desktop"]
        LLMs["Other LLMs via MCP"]
    end

    Obsidian --> Plugin
    Plugin -->|"HTTP POST /search"| API
    Claude -->|"stdio MCP"| MCP_SRV
    LLMs -->|"stdio MCP"| MCP_SRV
    API --> Embedder
    MCP_SRV --> Embedder
    Embedder --> DB
    Watcher -->|"monitors"| Notes
    Watcher -->|"triggers reindex"| API
    API --> DB
    MCP_SRV --> DB
```

---

## 2. Obsidian Plugin — Search Flow

User searches from inside Obsidian via the plugin.

```mermaid
sequenceDiagram
    actor User
    participant Obsidian as Obsidian App
    participant Plugin as Semantic Search Plugin
    participant API as FastAPI Server
    participant Embed as Embedder
    participant DB as sqlite-vec
    participant Rerank as CrossEncoder Reranker

    User->>Obsidian: Cmd+Shift+F
    Obsidian->>Plugin: Open SemanticSearchModal

    loop User types query
        User->>Plugin: Keystroke
        Note over Plugin: 300ms debounce resets
    end

    Plugin->>API: POST /search {query, top_k=10}
    API->>Embed: encode("search_query: " + query)
    Embed-->>API: float32[768]
    API->>DB: ANN search top-50
    DB-->>API: candidate chunks + distances
    API->>Rerank: CrossEncoder(query, chunks)
    Rerank-->>API: reranked scores
    API-->>Plugin: SearchResult[] top 10

    Plugin->>Obsidian: Render results in modal

    alt User clicks a note result
        User->>Plugin: Click result
        Plugin->>Obsidian: openLinkText(file_path)
        Obsidian->>User: Opens note scrolled to heading
    else User clicks a web or PDF result
        User->>Plugin: Click result
        Plugin->>Obsidian: Show preview panel with chunk content
    end
```

---

## 3. Obsidian Plugin — URL & PDF Ingestion Flow

User clips a web page or indexes a PDF from inside Obsidian.

```mermaid
sequenceDiagram
    actor User
    participant Obsidian as Obsidian App
    participant Plugin as Semantic Search Plugin
    participant API as FastAPI Server
    participant Fetch as httpx + trafilatura
    participant Pipeline as Indexing Pipeline
    participant DB as sqlite-vec

    alt Clip URL from clipboard
        User->>Obsidian: Cmd+P — Index URL from clipboard
        Plugin->>Plugin: navigator.clipboard.readText()
        Plugin->>API: POST /ingest/url {url, tags}
        API->>Fetch: httpx.get(url)
        Fetch-->>API: HTML
        API->>Fetch: trafilatura.extract(html)
        Fetch-->>API: clean text + title
    else Index a PDF file
        User->>Obsidian: Cmd+P — Index PDF file
        Plugin->>Obsidian: Open file picker
        User->>Plugin: Select PDF
        Plugin->>API: POST /ingest/pdf {file_path}
        API->>API: pymupdf4llm.to_markdown(path)
    end

    API->>Pipeline: chunk → embed → store
    Pipeline->>DB: INSERT chunks + embeddings
    DB-->>API: chunks_added: N
    API-->>Plugin: IngestResult
    Plugin->>Obsidian: Notice "Indexed N chunks from source"
```

---

## 4. Claude — MCP Query Flow

User asks Claude a question; Claude searches the vault autonomously.

```mermaid
sequenceDiagram
    actor User
    participant Claude as Claude Desktop
    participant MCP as FastMCP Server
    participant Embed as Embedder
    participant DB as sqlite-vec
    participant Rerank as CrossEncoder
    participant Vault as Obsidian Vault

    User->>Claude: What did I write about quantum computing?

    Claude->>MCP: search_notes(query, top_k=10)
    MCP->>Embed: encode("search_query: quantum computing")
    Embed-->>MCP: float32[768]
    MCP->>DB: ANN search top-50
    DB-->>MCP: candidates
    MCP->>Rerank: rerank candidates
    Rerank-->>MCP: top 10 SearchResult[]
    MCP-->>Claude: results with file_path + header_path + excerpt

    Claude->>MCP: get_note_content("Physics/Quantum.md")
    MCP->>Vault: read file
    Vault-->>MCP: full markdown text
    MCP-->>Claude: note content

    Claude->>User: Synthesized answer with citations and links
```

---

## 5. Claude — Ingestion & Index Management Flow

User asks Claude to index new content or manage the index.

```mermaid
sequenceDiagram
    actor User
    participant Claude as Claude Desktop
    participant MCP as FastMCP Server
    participant Fetch as httpx + trafilatura
    participant Pipeline as Indexing Pipeline
    participant DB as sqlite-vec

    alt Index a URL
        User->>Claude: Index this article for me — https://...
        Claude->>MCP: index_url(url, tags=["reading"])
        MCP->>Fetch: httpx.get(url)
        Fetch-->>MCP: HTML
        MCP->>Fetch: trafilatura.extract()
        Fetch-->>MCP: clean text
        MCP->>Pipeline: chunk → embed → store
        Pipeline->>DB: INSERT
        DB-->>MCP: chunks_added: 14
        MCP-->>Claude: IngestResult(chunks_added=14)
        Claude->>User: Done — indexed 14 chunks from the article

    else Check index status
        User->>Claude: How many notes are indexed?
        Claude->>MCP: get_index_status()
        MCP->>DB: SELECT COUNT stats
        DB-->>MCP: IndexStatus
        MCP-->>Claude: total_chunks 4521, total_documents 312
        Claude->>User: Your vault has 312 documents and 4521 chunks

    else Remove stale content
        User->>Claude: Remove the old article about X from the index
        Claude->>MCP: list_indexed_files(source_type="web")
        MCP-->>Claude: list of web URLs
        Claude->>MCP: remove_from_index(file_path="https://old-url.com")
        MCP->>DB: DELETE WHERE file_path = ?
        DB-->>MCP: chunks_removed: 8
        MCP-->>Claude: chunks_removed 8
        Claude->>User: Removed 8 chunks from the index
    end
```

---

## 6. Indexing Pipeline — Content Processing

How any source flows through chunking and storage.

```mermaid
flowchart TD
    A([Source]) --> B{Source type?}

    B -->|".md file"| C["python-frontmatter<br/>Strip YAML → store as metadata"]
    B -->|"PDF"| D["pymupdf4llm.to_markdown()<br/>Preserves tables, columns,<br/>infers headings from font size"]
    B -->|"URL"| E["httpx.get(url)<br/>trafilatura.extract(html)"]

    C --> F["markdown-it-py<br/>Parse token stream"]
    D --> F
    E --> F

    F --> G["Split on header boundaries<br/>Build breadcrumb: Note > Section > Sub"]

    G --> H{Block type?}

    H -->|"Regular text"| I{Token count?}
    H -->|"Markdown table"| J["Keep atomic<br/>repeat header row if split needed"]
    H -->|"Mermaid block"| K["Index DSL text as-is<br/>metadata: type=mermaid"]
    H -->|"Figure embed"| L["Index surrounding context<br/>metadata: has_figure=true"]
    H -->|"Callout block"| M["Atomic chunk<br/>metadata: callout_type"]

    I -->|"> 512 tokens"| N["nltk sentence split<br/>50-token overlap"]
    I -->|"< 64 tokens"| O["Merge with next sibling"]
    I -->|"64–512 tokens"| P["Keep as single chunk"]

    N --> Q["Dedup check:<br/>mtime in DB == current mtime?"]
    O --> Q
    P --> Q
    J --> Q
    K --> Q
    L --> Q
    M --> Q

    Q -->|"Unchanged"| R(["Skip ✓"])
    Q -->|"New or Modified"| S["sentence-transformers<br/>encode batch=32<br/>prefix: search_document"]

    S --> T["SQLite BEGIN IMMEDIATE<br/>INSERT OR REPLACE chunks<br/>INSERT OR REPLACE embeddings<br/>COMMIT"]
    T --> U["Delete stale chunks<br/>for same file_path"]
    U --> V(["Indexed ✓"])
```

---

## 7. File Watcher — Incremental Reindex Flow

How vault changes trigger automatic reindexing.

```mermaid
flowchart TD
    A["watchdog FSEventsObserver<br/>Vault root, recursive<br/>macOS FSEvents — zero polling"] --> B{Event}

    B -->|"on_modified / on_created"| C{File type?}
    B -->|"on_deleted"| D["Remove from index<br/>DELETE WHERE file_path = ?"]
    B -->|"on_moved"| E["Remove old path<br/>Schedule index new path"]

    C -->|".md"| F{In ignored path?}
    C -->|"other"| G(["Ignore"])

    F -->|".obsidian or excluded folders"| G
    F -->|"Normal note"| H["Cancel existing<br/>debounce timer for path"]

    H --> I["Start 2s debounce timer<br/>handles Obsidian autosave"]
    I --> J["Timer fires<br/>run indexing pipeline"]
    J --> K(["Index updated ✓"])

    subgraph Startup ["On Backend Startup"]
        S1["Walk vault for all .md files"] --> S2["Compare mtime vs DB"]
        S2 --> S3{Changed?}
        S3 -->|"New or Modified"| S4["Queue for indexing"]
        S3 -->|"Deleted"| S5["Remove from DB"]
        S3 -->|"Unchanged"| S6(["Skip ✓"])
        S4 --> S7["Run indexing pipeline"]
    end
```
