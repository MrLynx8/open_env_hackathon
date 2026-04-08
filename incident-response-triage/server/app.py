import uvicorn


def main() -> None:
    # Validate multi-mode contract while delegating to the existing API app.
    uvicorn.run("server:app", host="0.0.0.0", port=7860, workers=1)


if __name__ == "__main__":
    main()
