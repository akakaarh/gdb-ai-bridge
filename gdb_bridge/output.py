"""Semantic JSON output for debug context."""
import json


def format_context(ctx):
    """Convert DebugContext to JSON-serializable dict with semantic annotations."""
    data = ctx.to_dict()
    return data


def save_context(ctx, filepath):
    """Save DebugContext to JSON file."""
    data = format_context(ctx)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def print_context(ctx):
    """Print context as formatted JSON to stdout."""
    data = format_context(ctx)
    print(json.dumps(data, indent=2, ensure_ascii=False))
