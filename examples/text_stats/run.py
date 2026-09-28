"""Text statistics: counts and top words.

Contract: stdin {"text": str, "top": int?} -> stdout {"chars", "words", "lines", "top_words"}.
"""

import json
import re
import sys
from collections import Counter


def run(data: dict) -> dict:
    text = data["text"]
    words = re.findall(r"[A-Za-z0-9']+", text.lower())
    top = Counter(words).most_common(int(data.get("top", 3)))
    return {
        "chars": len(text),
        "words": len(words),
        "lines": len(text.splitlines()) if text else 0,
        "top_words": [{"word": w, "count": c} for w, c in sorted(top, key=lambda wc: (-wc[1], wc[0]))],
    }


if __name__ == "__main__":
    print(json.dumps(run(json.load(sys.stdin))))
