from smolagents import CodeAgent

CodeAgent(
    model=model,
    step_callbacks=[save_screenshot_callback],
    max_steps=20,
    verbosity_level=2,
)
