
# conferencing.py
import tkinter as tk
from tkinter import messagebox, simpledialog
import sqlite3
import cv2
import socket
import threading
import pyaudio
import numpy as np
from matching import match_users
class VideoConferencingApp:
    def __init__(self, username):
        self.username = username
        self.root = tk.Tk()
        self.root.title(f"AI Conferencing - {username}")
        self.root.configure(bg="#7A397B") 
         
        # Matching setup
        self.matches = self.get_matched_users()
        self.current_match_index = 0
        
        # Video and audio capture
        self.video_capture = cv2.VideoCapture(0)
        self.audio_input = pyaudio.PyAudio()
        
        # Network setup
        self.setup_network_components()
        
        # UI Components
        self.create_ui()
        
    def get_matched_users(self):
        try:
            # Use matching module to find users with similar interests
            matches = match_users(self.username)
            return matches
        except Exception as e:
            messagebox.showerror("Matching Error", str(e))
            return []
    
    def setup_network_components(self):
        # Socket setup for peer-to-peer communication
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(('localhost', 0))  # Random available port
        self.sock.listen(1)
    
    def create_ui(self):
        # Matched Users Frame
        matches_frame = tk.Frame(self.root, bg= "#7A397B")
        matches_frame.pack(side=tk.LEFT, padx=10, pady=10,)
        
        tk.Label(matches_frame, text="Matched Users", fg="white", bg="#7A397B", font=("Arial", 12, "bold")).pack()
        
        # Matched Users List
        
        self.matched_users_listbox = tk.Listbox(matches_frame, width=30,height=10, bg="#2E2E2E", fg="white", font=("Arial", 10))
        self.matched_users_listbox.pack(fill=tk.BOTH, expand=True)
        
        # Populate matched users
        for match, similarity in self.matches:
            self.matched_users_listbox.insert(tk.END, 
                f"{match} (Similarity: {similarity*100:.2f}%)")
        
        # Control Buttons
        btn_frame = tk.Frame(matches_frame)
        btn_frame.pack(pady=10)
        
        skip_btn = tk.Button(btn_frame, text="Skip", command=self.skip_match,bg="#7A397B", fg="black", font=("Arial", 10))
        skip_btn.pack(side=tk.LEFT, padx=5)
        
        connect_btn = tk.Button(btn_frame, text="Connect", command=self.connect_to_user, bg="#7A397B", fg="black", font=("Arial", 10))
        connect_btn.pack(side=tk.LEFT, padx=5)
        
        # Video and Chat Frame
        video_frame = tk.Frame(self.root, bg="#7A397B")
        video_frame.pack(side=tk.RIGHT, padx=10, pady=10, fill = tk.BOTH, expand = True)
        
        # Local Video Display
        self.local_video_label = tk.Label(video_frame)
        self.local_video_label.pack()
        
        # Remote Video S50
        # SDisplay
        self.remote_video_label = tk.Label(video_frame)
        self.remote_video_label.pack()
        
        # Chat Components
        self.chat_text = tk.Text(video_frame, height=10, width=50, bg="#2E2E2E", fg="white", font=("Arial", 10))
        self.chat_text.pack(pady=10)
        
        self.chat_entry = tk.Entry(video_frame, width=50, bg="#ffffff", fg="black", font=("Arial", 10))
        self.chat_entry.pack()
        self.chat_entry.bind('<Return>', self.send_chat_message)
        
        # Start video streaming
        self.start_video_streaming()
    
    def start_video_streaming(self):
        # Local video streaming thread
        def stream_local_video():
            while True:
                ret, frame = self.video_capture.read()
                if ret:
                    # Convert frame for Tkinter
                    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    photo = tk.PhotoImage(data=cv2.imencode('.ppm', rgb_frame)[1].tobytes())
                    self.local_video_label.configure(image=photo)
                    self.local_video_label.image = photo
        
        threading.Thread(target=stream_local_video, daemon=True).start()
    
    def skip_match(self):
        # Move to next matched user
        if self.matches:
            self.current_match_index = (self.current_match_index + 1) % len(self.matches)
            self.matched_users_listbox.selection_clear(0, tk.END)
            self.matched_users_listbox.selection_set(self.current_match_index)
            self.matched_users_listbox.see(self.current_match_index)
        else:
            messagebox.showinfo("No Matches", "No more matched users available.")
    
    def connect_to_user(self):
        # Get selected user
        selection = self.matched_users_listbox.curselection()
        if selection:
            selected_user = self.matches[selection[0]][0]
            
            # Initiate connection dialog
            messagebox.showinfo("Connecting", f"Connecting to {selected_user}")
            
            # Here you would implement actual connection logic
            # This could involve signaling, WebRTC, or socket-based communication
    
    def send_chat_message(self, event=None):
        message = self.chat_entry.get()
        if message:
            # Implement message sending logic
            self.chat_text.insert(tk.END, f"You: {message}\n")
            self.chat_entry.delete(0, tk.END)
    
    def run(self):
        self.root.mainloop()
        
        # Cleanup
        self.video_capture.release()
        cv2.destroyAllWindows()

def start_video_conferencing(username):
    app = VideoConferencingApp(username)
    app.run()

# For direct testing
if __name__ == "__main__":
    username = input("Enter your username: ")
    start_video_conferencing(username)
