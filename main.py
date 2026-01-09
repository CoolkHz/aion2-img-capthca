from fastapi import FastAPI
from src.api import router
from src.settings import load_env


def create_app() -> FastAPI:
    load_env()
    app = FastAPI(title="验证码识别 API")
    app.include_router(router)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
