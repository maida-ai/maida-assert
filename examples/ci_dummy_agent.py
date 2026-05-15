"""Minimal traced agent used by CI to smoke-test the Maida assert action."""

from maida import trace, record_tool_call, record_llm_call


@trace(name="ci_dummy_agent")
def run_agent():
    record_tool_call(name="noop", args={"input": "hello"}, result={"ok": True})
    record_llm_call(
        model="stub",
        prompt="Say hello",
        response="Hello!",
        usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
    )


if __name__ == "__main__":
    run_agent()
