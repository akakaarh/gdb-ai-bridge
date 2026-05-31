"""Semantic JSON output for debug context."""
import json


def context_to_dict(ctx):
    """Convert DebugContext to JSON-serializable dict with semantic annotations."""
    data = ctx.to_dict()
    return data


def save_context(ctx, filepath):
    """Save DebugContext to JSON file."""
    data = context_to_dict(ctx)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def print_context(ctx):
    """Print context as formatted JSON to stdout."""
    data = context_to_dict(ctx)
    print(json.dumps(data, indent=2, ensure_ascii=False))
