import tkinter as tk
from tkinter import messagebox
import sqlite3
import os

def open_registration_window():
    # Make sure database folder exists
    os.makedirs('database', exist_ok=True)
    conn = sqlite3.connect('database/users.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            interests TEXT
        )
    ''')
    conn.commit()
    conn.close()

    reg_window = tk.Tk()
    reg_window.title("Register")
    reg_window.geometry("400x300")
    reg_window.configure(bg="#7A397B")

    tk.Label(reg_window, text="Username", fg="#ffffff", bg="#7A397B").pack()
    username_entry = tk.Entry(reg_window, bg="#ffffff", fg="#000000", relief="flat", width=25)
    username_entry.pack(pady=5, ipady=3)

    tk.Label(reg_window, text="Password", fg="#ffffff", bg="#7A397B").pack()
    password_entry = tk.Entry(reg_window, show="*", bg="#ffffff", fg="#000000", relief="flat", width=25)
    password_entry.pack(pady=5, ipady=3)

    def register_user():
        username = username_entry.get()
        password = password_entry.get()

        if username == "" or password == "":
            messagebox.showerror("Error", "Please fill all fields!")
            return

        conn = sqlite3.connect('database/users.db')
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
            conn.commit()
            messagebox.showinfo("Registration", "User registered successfully!")
            reg_window.destroy()
        except sqlite3.IntegrityError:
            messagebox.showerror("Error", "Username already exists!")
        finally:
            conn.close()

    tk.Button(reg_window, text="Register", command=register_user, bg="#C8A2C8", fg="#000000").pack(pady=5)

    reg_window.mainloop()
if __name__ == "__main__":
    open_registration_window()
