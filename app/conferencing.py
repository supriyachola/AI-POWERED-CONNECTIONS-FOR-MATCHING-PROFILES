
#import tkinter as tk
from tkinter import messagebox
import cv2
import threading
import socket
import pyaudio
import datetime
import numpy as np
from matching import match_users

class VideoConferencingApp:
    def __init__(self, username):
        self.username = username
        self.root = tk.Tk()
        self.root.title(f"AI Conferencing - {username}")
        self.root.configure(bg="#7A397B")

        self.matches = self.get_matched_users()
        self.current_match_index = 0

        self.video_capture = cv2.VideoCapture(0)

        self.create_ui()

    def get_matched_users(self):
        try:
            matches = match_users(self.username)
            return matches
        except Exception as e:
            messagebox.showerror("Matching Error", str(e))
            return []

    def create_ui(self):
        matches_frame = tk.Frame(self.root, bg="#7A397B")
        matches_frame.pack(side=tk.LEFT, padx=10, pady=10)

        tk.Label(matches_frame, text="Matched Users", fg="white", bg="#7A397B", font=("Arial", 12, "bold")).pack()

        self.matched_users_listbox = tk.Listbox(matches_frame, width=30, height=10, bg="#2E2E2E", fg="white", font=("Arial", 10))
        self.matched_users_listbox.pack(fill=tk.BOTH, expand=True)

        for match, similarity in self.matches:
            self.matched_users_listbox.insert(tk.END, f"{match} ({similarity*100:.2f}%)")

        btn_frame = tk.Frame(matches_frame, bg="#7A397B")
        btn_frame.pack(pady=10)

        tk.Button(btn_frame, text="Skip", command=self.skip_match, bg="#C8A2C8", fg="black").pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Connect", command=self.connect_to_user, bg="#C8A2C8", fg="black").pack(side=tk.LEFT, padx=5)

        video_frame = tk.Frame(self.root, bg="#7A397B")
        video_frame.pack(side=tk.RIGHT, padx=10, pady=10, fill=tk.BOTH, expand=True)

        self.local_video_label = tk.Label(video_frame)
        self.local_video_label.pack()

        self.chat_text = tk.Text(video_frame, height=10, width=50, bg="#2E2E2E", fg="white", font=("Arial", 10))
        self.chat_text.pack(pady=10)

        self.chat_entry = tk.Entry(video_frame, width=50, bg="#ffffff", fg="black", font=("Arial", 10))
        self.chat_entry.pack()
        self.chat_entry.bind('<Return>', self.send_chat_message)

        tk.Button(btn_frame, text="Skip", command=self.skip_match, bg="#C8A2C8", fg="black").pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Connect", command=self.connect_to_user, bg="#C8A2C8", fg="black").pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="End", command=self.end_conference, bg="#FF6666", fg="white").pack(side=tk.LEFT, padx=5)

        self.start_video_streaming()

    def start_video_streaming(self):
        def stream_video():
            while True:
                ret, frame = self.video_capture.read()
                if ret:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    img = cv2.resize(frame, (300, 200))
                    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                    img = cv2.imencode('.png', img)[1].tobytes()
                    photo = tk.PhotoImage(data=img)
                    self.local_video_label.configure(image=photo)
                    self.local_video_label.image = photo

        threading.Thread(target=stream_video, daemon=True).start()

    def skip_match(self):
        if self.matches:
            self.current_match_index = (self.current_match_index + 1) % len(self.matches)
            self.matched_users_listbox.selection_clear(0, tk.END)
            self.matched_users_listbox.selection_set(self.current_match_index)
            self.matched_users_listbox.see(self.current_match_index)

    def connect_to_user(self):
        selection = self.matched_users_listbox.curselection()
        if selection:
            selected_user = self.matches[selection[0]][0]
            messagebox.showinfo("Connecting", f"Connecting to {selected_user}...")

    def send_chat_message(self, event=None):
        message = self.chat_entry.get().strip()
        if message:
            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            self.chat_text.config(state=tk.NORMAL)
            self.chat_text.insert(tk.END, f"\n[You | {timestamp}]: {message}", "you")
            self.chat_text.insert(tk.END, "\n")
            self.chat_text.tag_config("you", foreground="#66D9EF", font=("Arial", 10, "bold"))
            self.chat_text.config(state=tk.DISABLED)
            self.chat_entry.delete(0, tk.END)

            # Simulate partner reply
            self.root.after(1000, lambda: self.receive_message(f"Got your message: '{message}'"))

    def receive_message(self, message):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.chat_text.config(state=tk.NORMAL)
        self.chat_text.insert(tk.END, f"\n[Partner | {timestamp}]: {message}", "partner")
        self.chat_text.insert(tk.END, "\n")
        self.chat_text.tag_config("partner", foreground="#FFD700", font=("Arial", 10, "italic"))
        self.chat_text.config(state=tk.DISABLED)
    
    def end_conference(self):
        self.video_capture.release()
        cv2.destroyAllWindows()
        self.root.destroy()
        messagebox.showinfo("Goodbye!", "Thanks for connecting!\nSee you next time!")


    def run(self):
        self.root.mainloop()
        self.video_capture.release()
        cv2.destroyAllWindows()

def start_video_conferencing(username):
    app = VideoConferencingApp(username)
    app.run()
