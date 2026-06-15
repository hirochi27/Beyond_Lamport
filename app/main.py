from fastapi import FastAPI

app = FastAPI()

#/api/healthへGETリクエストが来たら、直下のhealth()を実行する
@app.get("/api/health")
def health():
    return {"status": "ok"}