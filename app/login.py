import tkinter as tk
from tkinter import messagebox
from register import open_registration_window
from interests import open_interests_selection
import sqlite3

def open_login_window():
    main_window = tk.Tk()
    main_window.title("AI powered matching profile")
    main_window.geometry("400x300")
    main_window.configure(bg="#7A397B")  

    tk.Label(main_window, text="Welcome to AI Conferencing App", font=("Arial", 16, "bold"), fg="#ffffff", bg="#7A397B").pack(pady=10)


    tk.Label(main_window, text="Username", fg ="#ffffff", bg="#7A397B").pack()
    login_username_entry = tk.Entry(main_window, bg ="#ffffff", fg="#000000",relief="flat", width=25)
    login_username_entry.pack(pady=5, ipady=3)

    tk.Label(main_window, text="Password", fg="#ffffff", bg="#7A397B").pack()
    login_password_entry = tk.Entry(main_window, show="*", bg ="#ffffff", fg="#000000",relief="flat", width=25)
    login_password_entry.pack(pady=5, ipady=3)

    def login_user():
        username = login_username_entry.get()
        password = login_password_entry.get()

        conn = sqlite3.connect('database/users.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ? AND password = ?", (username, password))
        user = cursor.fetchone()
        conn.close()

        if user:
            messagebox.showinfo("Login", "Login successful!")
            main_window.destroy()
            open_interests_selection(username)
        else:
            messagebox.showerror("Login", "Invalid username or password!")

    tk.Button(main_window, text="Login", command=login_user, bg ="#C8A2C8",fg="#000000").pack(pady=5)
    tk.Button(main_window, text="Register", command=open_registration_window,bg ="#C8A2C8",fg="#000000" ).pack(pady=5)
    main_window.mainloop()

from login import open_login_window

if __name__ == "__main__":
    open_login_window()
