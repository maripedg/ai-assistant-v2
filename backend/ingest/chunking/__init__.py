"""Compatibility shim mapping to loader-backed chunking modules."""

from backend.ingest.loaders.chunking.block_types import *  # noqa: F401,F403
from backend.ingest.loaders.chunking.block_cleaner import *  # noqa: F401,F403
from backend.ingest.loaders.chunking.char_chunker import *  # noqa: F401,F403
from backend.ingest.loaders.chunking.token_chunker import *  # noqa: F401,F403
from backend.ingest.loaders.chunking.toc_utils import *  # noqa: F401,F403
from backend.ingest.loaders.chunking.structured_docx_chunker import *  # noqa: F401,F403
from backend.ingest.loaders.chunking.structured_pdf_chunker import *  # noqa: F401,F403
from backend.ingest.loaders.chunking.toc_section_docx_chunker import *  # noqa: F401,F403
