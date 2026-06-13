"""Веб-морда для Excel-агента: задача + загрузка/скачивание .xlsx, защита паролем.

Запуск (внутри контейнера):
    python webapp.py            # слушает 0.0.0.0:WEB_PORT (по умолчанию 8600)

Переменные окружения:
    WEB_USER       логин (по умолчанию admin)
    WEB_PASSWORD   пароль (ОБЯЗАТЕЛЬНО; без него запуск откажет)
    WEB_PORT       порт (по умолчанию 8600)
    WEB_PROFILE    профиль агента по умолчанию (skill)
    WEB_WORKDIR    рабочая папка (по умолчанию runs/web)
+ переменные модели из .env (OPENAI_BASE_URL, API_KEY, MODEL_NAME, ...).
"""
from __future__ import annotations

import os
import secrets
import shutil
import sys
import threading
from pathlib import Path

import uvicorn
from fastapi import (Depends, FastAPI, File, Form, HTTPException, UploadFile,
                     status)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

sys.path.insert(0, str(Path(__file__).parent / "src"))
from excel_agent.config import DATA_SOURCE  # noqa: E402

USER = os.getenv("WEB_USER", "admin")
PASSWORD = os.getenv("WEB_PASSWORD", "")
PORT = int(os.getenv("WEB_PORT", "8600"))
DEFAULT_PROFILE = os.getenv("WEB_PROFILE", "skill")
WORKDIR = Path(os.getenv("WEB_WORKDIR", "runs/web")).resolve()
WORKDIR.mkdir(parents=True, exist_ok=True)

if not PASSWORD:
    raise SystemExit("WEB_PASSWORD не задан — откажусь стартовать без пароля.")

app = FastAPI()
security = HTTPBasic()
run_lock = threading.Lock()  # агент не любит параллельные запуски (rate limit + общий workdir)


def auth(creds: HTTPBasicCredentials = Depends(security)) -> bool:
    ok = (secrets.compare_digest(creds.username, USER)
          and secrets.compare_digest(creds.password, PASSWORD))
    if not ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Неверный логин или пароль",
                            headers={"WWW-Authenticate": "Basic"})
    return True


def list_xlsx() -> list[dict]:
    out = []
    for f in sorted(WORKDIR.glob("*.xlsx")):
        out.append({"name": f.name, "size": f.stat().st_size})
    return out


@app.post("/api/upload")
def upload(_: bool = Depends(auth), file: UploadFile = File(...)):
    name = Path(file.filename).name
    if not name.lower().endswith(".xlsx"):
        raise HTTPException(400, "Только .xlsx")
    dest = WORKDIR / name
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return {"ok": True, "files": list_xlsx()}


@app.post("/api/fresh")
def fresh(_: bool = Depends(auth)):
    for f in DATA_SOURCE.glob("*.xlsx"):
        shutil.copy2(f, WORKDIR / f.name)
    return {"ok": True, "files": list_xlsx()}


@app.get("/api/files")
def files(_: bool = Depends(auth)):
    return {"files": list_xlsx()}


@app.get("/download/{name}")
def download(name: str, _: bool = Depends(auth)):
    f = (WORKDIR / Path(name).name)
    if not f.exists():
        raise HTTPException(404, "нет файла")
    return FileResponse(f, filename=f.name)


@app.post("/api/run")
def run(_: bool = Depends(auth), task: str = Form(...),
        profile: str = Form(DEFAULT_PROFILE)):
    if not task.strip():
        raise HTTPException(400, "пустая задача")
    if profile not in ("baseline", "skill", "skill_subagent"):
        profile = DEFAULT_PROFILE
    if not run_lock.acquire(blocking=False):
        raise HTTPException(409, "агент уже выполняет задачу — подождите")
    try:
        # импорт тяжёлый — внутри, чтобы старт сервера был быстрым
        from excel_agent.agent import build_agent, run_task
        agent = build_agent(profile, WORKDIR)
        res = run_task(agent, task)
        return JSONResponse({"answer": res.get("answer", ""),
                             "tokens": res.get("tokens"),
                             "steps": res.get("steps"),
                             "files": list_xlsx()})
    except Exception as e:  # noqa: BLE001 — показать причину (часто rate limit)
        return JSONResponse(status_code=502,
                            content={"error": f"{type(e).__name__}: {e}"[:1500]})
    finally:
        run_lock.release()


HTML = """<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Excel-агент</title><style>
 :root{--bg:#0f1220;--card:#191d2e;--acc:#21a86b;--text:#e7e9f0;--muted:#9aa0b4;}
 *{box-sizing:border-box} body{margin:0;font-family:system-ui,Segoe UI,sans-serif;background:#0f1220;color:#e7e9f0}
 .wrap{max-width:860px;margin:0 auto;padding:24px 16px 80px}
 h1{font-size:1.3rem} h1 span{color:#21a86b}
 .sub{color:#9aa0b4;font-size:.9rem;margin-bottom:18px}
 .card{background:#191d2e;border:1px solid #2a3050;border-radius:12px;padding:16px;margin:12px 0}
 textarea{width:100%;min-height:74px;background:#0f1220;color:#e7e9f0;border:1px solid #2a3050;border-radius:8px;padding:10px;font-size:1rem}
 select,input[type=file]{background:#0f1220;color:#e7e9f0;border:1px solid #2a3050;border-radius:8px;padding:8px}
 button{background:#21a86b;color:#04130b;border:0;border-radius:8px;padding:10px 18px;font-size:1rem;font-weight:600;cursor:pointer}
 button:disabled{opacity:.5;cursor:wait}
 .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px}
 .muted{color:#9aa0b4;font-size:.85rem}
 .answer{white-space:pre-wrap;line-height:1.55;border-left:3px solid #21a86b;padding:10px 12px;background:#0f1220;border-radius:8px}
 a{color:#21a86b} .file{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #2a3050}
 .err{color:#ff6b6b}
</style></head><body><div class="wrap">
<h1>Excel <span>·</span> агент</h1>
<div class="sub">задача → агент правит .xlsx (pandas/openpyxl) → скачиваешь результат</div>

<div class="card">
 <b>1. Данные</b>
 <div class="row">
   <button onclick="fresh()">Загрузить демо-данные</button>
   <label class="muted">или свой файл: <input type="file" id="up" accept=".xlsx"></label>
   <button onclick="upload()">Загрузить</button>
 </div>
 <div id="files" class="muted" style="margin-top:10px">…</div>
</div>

<div class="card">
 <b>2. Задача</b>
 <textarea id="task" placeholder="Напр.: Очисти столбец 'Выручка (грязная)' в sales_2025.xlsx и добавь числовой столбец 'Выручка'"></textarea>
 <div class="row">
   <select id="profile">
     <option value="skill" selected>skill</option>
     <option value="baseline">baseline</option>
     <option value="skill_subagent">skill_subagent</option>
   </select>
   <button id="go" onclick="run()">Выполнить</button>
   <span id="status" class="muted"></span>
 </div>
</div>

<div class="card" id="out" style="display:none">
 <b>Результат</b>
 <div id="answer"></div>
</div>
</div><script>
async function j(u,o){const r=await fetch(u,o);const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||d.detail||('HTTP '+r.status));return d}
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}
function renderFiles(fs){const el=document.getElementById('files');if(!fs||!fs.length){el.innerHTML='Файлов нет — загрузи демо или свой .xlsx';return}
 el.innerHTML=fs.map(f=>`<div class="file"><span>${esc(f.name)} <span class="muted">(${(f.size/1024).toFixed(1)} КБ)</span></span><a href="download/${encodeURIComponent(f.name)}">скачать</a></div>`).join('')}
async function refresh(){try{const d=await j('api/files');renderFiles(d.files)}catch(e){document.getElementById('files').innerHTML='<span class="err">'+esc(e.message)+'</span>'}}
async function fresh(){try{const d=await j('api/fresh',{method:'POST'});renderFiles(d.files)}catch(e){alert(e.message)}}
async function upload(){const f=document.getElementById('up').files[0];if(!f){alert('выбери файл');return}
 const fd=new FormData();fd.append('file',f);try{const d=await j('api/upload',{method:'POST',body:fd});renderFiles(d.files)}catch(e){alert(e.message)}}
async function run(){const task=document.getElementById('task').value.trim();if(!task){alert('впиши задачу');return}
 const profile=document.getElementById('profile').value;const go=document.getElementById('go');go.disabled=true;
 const st=document.getElementById('status');st.textContent='агент работает… (может занять минуту-другую)';
 const out=document.getElementById('out');out.style.display='block';document.getElementById('answer').innerHTML='…';
 const fd=new FormData();fd.append('task',task);fd.append('profile',profile);
 try{const d=await j('api/run',{method:'POST',body:fd});
   document.getElementById('answer').innerHTML='<div class="answer">'+esc(d.answer||'(пустой ответ)')+'</div>'+
     '<div class="muted" style="margin-top:8px">токены: '+(d.tokens??'?')+' · сообщений: '+(d.steps??'?')+'</div>';
   renderFiles(d.files);st.textContent='готово';
 }catch(e){document.getElementById('answer').innerHTML='<div class="err">'+esc(e.message)+'</div>';st.textContent='ошибка'}
 go.disabled=false}
refresh();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index(_: bool = Depends(auth)):
    return HTML


if __name__ == "__main__":
    ssl_cert = os.getenv("WEB_SSL_CERT", "")
    ssl_key = os.getenv("WEB_SSL_KEY", "")
    use_tls = bool(ssl_cert and ssl_key and Path(ssl_cert).exists() and Path(ssl_key).exists())
    scheme = "https" if use_tls else "http"
    print(f"Excel-агент web: {scheme}://0.0.0.0:{PORT} "
          f"(user={USER}, profile={DEFAULT_PROFILE}, tls={'on' if use_tls else 'off'})")
    kwargs = dict(host="0.0.0.0", port=PORT, log_level="warning")
    if use_tls:
        kwargs.update(ssl_certfile=ssl_cert, ssl_keyfile=ssl_key)
    uvicorn.run(app, **kwargs)
