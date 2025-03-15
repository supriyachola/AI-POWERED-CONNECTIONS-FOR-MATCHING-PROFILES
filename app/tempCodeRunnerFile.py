 tk.Label(interests_window, text="Select your interests:").pack(pady=10)

    interests = ["Technology", "Sports", "Travel", "Music", "Gaming", "Cooking", "Movies", "Art", "Science"]
    interest_vars = {interest: tk.BooleanVar() for interest in interests}

    for interest, var in interest_vars.items():
        tk.Checkbutton(interests_window, text=interest, variable=var).pack(anchor='w')

    def save_interests():
        selected_interests = [interest for interest, var in interest_vars.items() if var.get()]
        conn = sqlite3.connect('database/users.db')