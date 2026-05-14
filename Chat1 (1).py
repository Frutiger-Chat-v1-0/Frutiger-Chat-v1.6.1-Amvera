import threading
import asyncio
import hashlib
import sqlite3
try:
    import tkinter as tk
    from tkinter import scrolledtext, messagebox
except ImportError:
    # Если мы на сервере, где нет Tkinter, создаем "пустышки", чтобы код не падал
    tk = None
    messagebox = None
    print("Tkinter не найден. Работа в режиме сервера.")

import os
from pywebio import start_server
from pywebio.input import *
from pywebio.output import *
from pywebio.session import run_async, defer_call, set_env, run_js, eval_js
from pywebio.pin import put_input, pin
from datetime import datetime
import base64
import time

# --- СПИСОК ДЛЯ ФИЛЬТРАЦИИ МАТОВ ---
BAD_WORDS = [
    "сука", "сучка", "бля", "блять", "блядь", "хуй", "хуя", "охуеть", "хуево",
    "пиздец", "пизда", "пидорас", "пидор", "гандон", "ебать", "еблан", "ебаный",
    "мудак", "долбоеб", "сволочь", "говно", "жопа"
]

def filter_message(text):
    """Заменяет плохие слова из списка на звездочки ***"""
    if not isinstance(text, str):
        return text
    words = text.split()
    clean_words = []
    for word in words:
        # Очищаем слово от знаков препинания для проверки
        test_word = word.lower().strip(".,!?:;")
        is_bad = False
        for bad in BAD_WORDS:
            if bad in test_word:
                is_bad = True
                break
        
        if is_bad:
            clean_words.append("🌊" * len(word))
        else:
            clean_words.append(word)
    return " ".join(clean_words)

# --- 1. БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("users.db")
    conn.execute('CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT, role TEXT DEFAULT "user")')
    conn.execute('CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, username TEXT, msg_type TEXT, content TEXT, timestamp TEXT, file_data BLOB)')
    conn.execute('CREATE TABLE IF NOT EXISTS group_members (group_name TEXT, username TEXT)')
    conn.commit()
    conn.close()

def register_user(u, p):
    h = hashlib.sha256(p.encode()).hexdigest()
    try:
        conn = sqlite3.connect("users.db")
        conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)", (u, h, 'user'))
        conn.commit(); return True
    except: return False
    finally: conn.close()

def check_user(u, p):
    h = hashlib.sha256(p.encode()).hexdigest()
    conn = sqlite3.connect("users.db")
    res = conn.execute("SELECT role FROM users WHERE username=? AND password_hash=?", (u, h)).fetchone()
    conn.close()
    return res[0] if res else None

def create_group(group_name, creator):
    conn = sqlite3.connect("users.db")
    conn.execute("INSERT INTO group_members VALUES (?, ?)", (group_name, creator))
    conn.commit(); conn.close()

def invite_to_group(group_name, username):
    conn = sqlite3.connect("users.db")
    exists = conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
    if exists:
        conn.execute("INSERT INTO group_members VALUES (?, ?)", (group_name, username))
        conn.commit(); conn.close(); return True
    conn.close(); return False

def get_user_groups(username):
    conn = sqlite3.connect("users.db")
    res = conn.execute("SELECT group_name FROM group_members WHERE username=?", (username,)).fetchall()
    conn.close()
    return [r[0] for r in res]

def save_msg(cid, u, tp, cont, tm, fdata=None):
    # Применяем фильтрацию к тексту сообщения
    clean_content = filter_message(str(cont)) if tp == 'text' else cont
    conn = sqlite3.connect("users.db")
    conn.execute("INSERT INTO messages (chat_id, username, msg_type, content, timestamp, file_data) VALUES (?, ?, ?, ?, ?, ?)", (cid, u, tp, str(clean_content), tm, fdata))
    conn.commit(); conn.close()

def delete_msg_logic(mid):
    conn = sqlite3.connect("users.db")
    conn.execute("DELETE FROM messages WHERE id=?", (mid,))
    conn.commit(); conn.close()

def load_all_messages():
    conn = sqlite3.connect("users.db")
    cursor = conn.execute("SELECT id, chat_id, username, msg_type, content, timestamp, file_data FROM messages")
    rows = cursor.fetchall(); conn.close()
    new_data = {}
    for r in rows:
        mid, cid, u, tp, cont, tm, fdata = r
        if cid not in new_data: new_data[cid] = []
        new_data[cid].append((mid, u, tp, cont, tm, fdata))
    return new_data

# --- ГЛОБАЛЫ ---
online_users = set(); banned_users = set()
THEMES = {
    "Пастель": "linear-gradient(135deg, #cfe9d5 0%, #f6e5bc 100%)",
    "Классик": "linear-gradient(135deg, #a1c4fd 0%, #c2e9fb 100%)",
    "Закат": "linear-gradient(135deg, #ff9a9e 0%, #fecfef 100%)",
    "Океан": "linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)"
}
def get_time(): return datetime.now().strftime("%H:%M")

# --- 2. ВЕБ-ИНТЕРФЕЙС ---
async def web_main():
    global online_users, banned_users
    set_env(title="Frutiger Chat 🌊", output_animation=False)
    
    # --- ЗАГРУЗКА ЛОГОТИПА ---
    logo_base64 = ""
    if os.path.exists("logo.png"):
        with open("logo.png", "rb") as f:
            logo_base64 = base64.b64encode(f.read()).decode()

    # Музыкальный блок (оставляем как был)
    music_base64 = ""
    # ... твой код с музыкой ...

    if os.path.exists("music.mp3"):
        with open("music.mp3", "rb") as f:
            music_base64 = base64.b64encode(f.read()).decode()
        
        put_html(f"""
        <audio id="bg-m" loop><source src="data:audio/mpeg;base64,{music_base64}" type="audio/mpeg"></audio>
        <button id="m-btn" style="position:fixed; top:10px; right:10px; z-index:9999; padding:8px 15px; border-radius:20px; border:none; background:rgba(255,255,255,0.6); backdrop-filter:blur(10px); cursor:pointer;"> Музыка </button>
        <script>
            var a=document.getElementById('bg-m'); var b=document.getElementById('m-btn');
            b.onclick=function(){{ a.volume=0.2; a.play(); b.style.display='none'; }};
            document.addEventListener('click', function(){{ if(a.paused){{ a.volume=0.2; a.play(); b.style.display='none'; }} }}, {{once:true}});
        </script>
        """)

    choice = await actions("🌊Frutiger Chat", ["Вход", "Регистрация"])
    nickname, user_role = "", "user"
    while not nickname:
        info = await input_group(choice, [input("Логин", name="user", required=True), input("Пароль", name="pwd", type=PASSWORD, required=True)])
        if choice == "Регистрация":
            if register_user(info['user'], info['pwd']): toast("Успех!"); choice = "Вход"
            else: toast("Ошибка!", color='error')
        else:
            if info['user'] in banned_users: put_error("Бан!"); return
            res = check_user(info['user'], info['pwd'])
            if res: nickname, user_role = info['user'], res
            else: toast("Неверные данные!", color='error')

    online_users.add(nickname); current_chat = "Общий чат"; force_update = False

    conn = sqlite3.connect("users.db")
    res = conn.execute("SELECT MAX(id) FROM messages").fetchone()
    last_msg_id = res[0] if res and res[0] is not None else 0
    conn.close()

    put_html(f"<style>:root {{ --bg-aero: {THEMES['Пастель']}; }} body {{ background: var(--bg-aero) fixed; font-family: 'Segoe UI', sans-serif; margin: 0; }} .sidebar {{ background: rgba(255, 255, 255, 0.4); backdrop-filter: blur(15px); border-right: 1px solid rgba(255,255,255,0.5); }} .chat-window {{ background: rgba(255, 255, 255, 0.3); backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.5); border-radius: 20px; }} .bubble {{ padding: 12px 18px; border-radius: 20px; margin: 8px 0; max-width: 80%; border: 1px solid rgba(255,255,255,0.4); }} .my-msg {{ background: linear-gradient(180deg, #74c69d 0%, #40916c 100%) !important; color: white; float: right; clear: both; }} .other-msg {{ background: #fff; color: #222; float: left; clear: both; }} .admin-msg {{ background: linear-gradient(180deg, #4facfe 0%, #00f2fe 100%) !important; color: white; float: left; clear: both; }}</style>")

    put_scope('sidebar').style('position: fixed; left: 0; top: 0; width: 240px; height: 100%; padding: 20px; z-index: 1000;')
    with put_column().style('margin-left: 240px; padding: 20px;'):
        put_scope('header_area')
        put_html('<div class="chat-window">')
        put_scrollable(put_scope('msg_list'), height=480, keep_bottom=True)
        put_html('</div>')
        put_scope('footer_area').style('position: fixed; bottom: 0; left: 240px; right: 0; padding: 20px;')

    with use_scope('footer_area'):
        put_row([put_input('msg_pin', placeholder="Напишите..."), put_button("ОТПРАВИТЬ", color='success', onclick=lambda: run_async(send_msg_func()))], size='1fr auto')

    async def send_msg_func():
        val = await pin.msg_pin
        if val:
            save_msg(current_chat, nickname, 'text', val, get_time())
            run_js("document.querySelector('input[name=msg_pin]').value = ''")

    async def create_group_dialog():
        name = await input("Имя группы", required=True)
        create_group(name, nickname); toast(f"Группа {name} создана!")

    async def invite_dialog():
        if current_chat == "🏠Общий чат" or "_dialog_" in current_chat: toast("Выберите группу!", color='warn'); return
        target = await input("Кого пригласить?", required=True)
        if invite_to_group(current_chat, target): toast ("Успех!")
        else: toast("Не найден!", color='error')

    def change_chat(n):
        nonlocal current_chat, force_update
        if n != "🏠Общий чат" and n not in get_user_groups(nickname):
            current_chat = "_dialog_" + "_".join(sorted([nickname, n]))
        else: current_chat = n
        force_update = True
        with use_scope('header_area', clear=True): put_html(f'<h2> {n}</h2>')

    async def update_loop():
        nonlocal current_chat, user_role, force_update, last_msg_id
        last_count = -1
        while True:
            with use_scope('sidebar', clear=True):
            # --- КРУГЛЫЙ ЛОГОТИП (ОБРЕЗАЕМ БЕЛЫЕ УГЛЫ) ---
                if logo_base64:
                    put_html(f'''
                        <div style="text-align: center; margin-bottom: 15px;">
                            <img src="data:image/png;base64,{logo_base64}" 
                                style="width: 100px; border-radius: 50%; border: 2px solid rgba(255,255,255,0.5); box-shadow: 0 5px 15px rgba(0,0,0,0.2);">
                        </div>
                ''')

            
                put_html(f"<b>{nickname}</b><br><small>Статус: {user_role}</small><hr>")
                put_button(" Общий чат", onclick=lambda: change_chat("Общий чат"), small=True, outline=(current_chat != "Общий чат")).style('width:100%;')
                put_html('<br><b> 👥Группы:</b>')
                for g in get_user_groups(nickname):
                    put_button(f" {g}", onclick=lambda n=g: change_chat(n), small=True, color='warning', outline=(current_chat != g)).style('width:100%; margin-bottom:2px;')
                put_row([put_button(" 👥Группа", onclick=create_group_dialog, small=True, color='success'), put_button(" ✉Пригласить", onclick=invite_dialog, small=True, color='info')])
                put_html('<br><b> ЛС (Онлайн):</b>')
                for u in sorted(online_users):
                    if u != nickname: put_button(u, onclick=lambda t=u: change_chat(t), small=True, color='info').style('width:100%;')

            conn = sqlite3.connect("users.db")
            new_msgs = conn.execute("SELECT id, chat_id, username FROM messages WHERE id > ?", (last_msg_id,)).fetchall()
            conn.close()
            for mid, cid, sender in new_msgs:
                last_msg_id = mid
                if sender != nickname and cid != current_chat:
                    toast(f" Сообщение от {sender}")

            all_msgs = load_all_messages(); msgs = all_msgs.get(current_chat, [])
            if len(msgs) != last_count or force_update:
                with use_scope('msg_list', clear=True):
                    for mid, n, tp, dt, tm, fd in msgs:
                        style = "admin-msg" if n=="ADMIN" else ("my-msg" if n==nickname else "other-msg")
                        put_html(f'<div class="bubble {style}"><small>{n} {tm}</small><br>{dt}</div>')
                        if user_role in ['admin', 'mod'] or n == nickname:
                            put_button("удалить", onclick=lambda m=mid: [delete_msg_logic(m), toast("Удалено")], small=True, outline=True).style('color:red; border:none;')
                last_count = len(msgs); force_update = False; run_js("window.scrollTo(0, document.body.scrollHeight);")
            await asyncio.sleep(2.0)

    change_chat("Общий чат"); run_async(update_loop()); await asyncio.Event().wait()

# --- 3. АДМИНКА ---
def run_tk_window():
    if tk is None: # Если Tkinter не загрузился, просто выходим
        return
    root = tk.Tk()
    # ... остальной код твоей админки ...

    root = tk.Tk(); root.title("ADMIN PANEL"); root.geometry("600x600")
    top = tk.Frame(root); top.pack(fill='x', pady=5)
    users_list_gui = tk.Listbox(root, font=("Arial", 12)); users_list_gui.pack(fill='both', expand=True, padx=10)

    tk.Button(top, text=" MOD", bg="gold", command=lambda: [sqlite3.connect("users.db").execute("UPDATE users SET role='mod' WHERE username=?", (users_list_gui.get(tk.ACTIVE),)).connection.commit(), messagebox.showinfo("ОК", "Готово")]).pack(side='left', padx=10)
    tk.Button(top, text=" BAN", bg="red", fg="white", command=lambda: banned_users.add(users_list_gui.get(tk.ACTIVE))).pack(side='left', padx=10)
    tk.Button(top, text=" CLEAR ALL", bg="black", fg="white", command=lambda: [sqlite3.connect("users.db").execute("DELETE FROM messages").connection.commit(), messagebox.showinfo("OK", "Очищено")]).pack(side='right', padx=10)

    bot = tk.Frame(root); bot.pack(fill='x', pady=10)
    admin_entry = tk.Entry(bot, font=("Arial", 12)); admin_entry.pack(side='left', fill='x', expand=True, padx=10)
    tk.Button(bot, text="SEND AS ADMIN", bg="blue", fg="white", command=lambda: [save_msg("Общий чат", "ADMIN", "text", admin_entry.get(), get_time()), admin_entry.delete(0, 'end')]).pack(side='right', padx=10)

    def refresh():
        users_list_gui.delete(0, tk.END)
        conn = sqlite3.connect("users.db")
        for u in conn.execute("SELECT username FROM users").fetchall(): users_list_gui.insert(tk.END, u[0])
        conn.close(); root.after(5000, refresh)
    refresh(); root.mainloop()

if __name__ == "__main__":
    init_db()
    # Запускаем админку только если мы дома. В облаке Tkinter не сработает!
    if os.environ.get('RENDER') is None:
        threading.Thread(target=run_tk_window, daemon=True).start()
    
    # Порт для Render
    port = int(os.environ.get("PORT", 8080))
    start_server(web_main, port=port, host='0.0.0.0')

