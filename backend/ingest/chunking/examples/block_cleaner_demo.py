from backend.ingest.chunking.block_types import Block
from backend.ingest.chunking.block_cleaner import clean_blocks


def demo():
    raw_blocks = [
        Block(type="paragraph", text="Table of Contents\n1 Intro ..... 3\n2 Body ..... 5\nIntro starts", meta={"page": 1}),
        Block(type="paragraph", text="Confidential\nHeader Title\nMain content line 1\nMain content line 2\nPage 1 of 3", meta={"page": 1}),
        Block(type="paragraph", text="Confidential\nHeader Title\nMain content line 3\nMain content line 4\nPage 2 of 3", meta={"page": 2}),
    ]
    cleaned = clean_blocks("pdf", raw_blocks)
    print(f"Before: {len(raw_blocks)} blocks, After: {len(cleaned)} blocks")
    for blk in cleaned:
        print("---")
        print(blk.text)


if __name__ == "__main__":
    demo()
