"""Small text matching helpers."""


def contains_any(text: str, keywords: list[str]) -> bool:
    """Return True when any keyword appears in the input text."""
    return any(keyword in text for keyword in keywords)


def show_graph(graph, xray=False):
    """Display a LangGraph Mermaid diagram with fallback rendering."""
    from IPython.display import Image, Markdown

    drawable_graph = graph.get_graph(xray=xray)

    try:
        return Image(drawable_graph.draw_mermaid_png())
    except Exception as api_error:
        print(
            "Default renderer failed "
            f"({api_error}), falling back to pyppeteer..."
        )

    try:
        # Pyppeteer needs the notebook event loop to allow nested async calls.
        import nest_asyncio
        nest_asyncio.apply()

        from langchain_core.runnables.graph import MermaidDrawMethod

        return Image(
            drawable_graph.draw_mermaid_png(
                draw_method=MermaidDrawMethod.PYPPETEER
            )
        )
    except Exception as local_error:
        print(
            "Local pyppeteer renderer failed "
            f"({local_error}), showing Mermaid source instead."
        )
        mermaid_code = drawable_graph.draw_mermaid()
        return Markdown(f"```mermaid\n{mermaid_code}\n```")
