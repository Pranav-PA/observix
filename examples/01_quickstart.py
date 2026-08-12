"""Quickstart: instrument a function and see the telemetry.

Run:  python examples/01_quickstart.py

Prints spans to the console. Point at a real backend by changing *only* the
`exporters` argument --- no other line in this file moves.
"""

from observix import configure, flush, get_current_span, observe, observe_block

configure(
    service_name="quickstart",
    exporters=["console"],
    # Try these instead --- nothing below changes:
    #   exporters=["phoenix"]
    #   exporters=["langfuse"]
    #   exporters=["phoenix", "langfuse", "datadog"]
)


@observe
def add(a: int, b: int) -> int:
    """A plain function. Arguments and return value are captured."""
    return a + b


@observe(kind="chat", name="ask_model")
def ask_model(prompt: str) -> str:
    """A model call, recorded with AI-specific metadata."""
    answer = "4"  # stand-in for a real SDK call

    get_current_span().record_llm_call(
        provider="anthropic",
        request_model="claude-opus-4",
        input_messages=[{"role": "user", "content": prompt}],
        output_messages=[{"role": "assistant", "content": answer}],
        input_tokens=12,
        output_tokens=1,
        temperature=0.0,
    )
    return answer


@observe(kind="workflow")
def pipeline(question: str) -> str:
    """A workflow composing the two. Spans nest automatically."""
    with observe_block("preprocess") as span:
        cleaned = question.strip()
        span.set_io(input=question, output=cleaned)

    add(2, 2)
    return ask_model(cleaned)


if __name__ == "__main__":
    print(pipeline("  What is 2+2?  "))
    flush()  # short-lived process: flush before exit
