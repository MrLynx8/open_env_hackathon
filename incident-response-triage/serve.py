import uvicorn


def main() -> None:
    # Keep using the existing FastAPI app object in server.py.
    uvicorn.run("server:app", host="0.0.0.0", port=7860, workers=1)


if __name__ == "__main__":
    main()
