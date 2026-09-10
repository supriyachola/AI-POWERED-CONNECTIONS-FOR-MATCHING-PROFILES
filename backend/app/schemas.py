from pydantic import BaseModel, Field, field_validator

GENDERS = {"male", "female", "non_binary", "prefer_not_to_say"}
PREFERRED_GENDERS = {"any", "male", "female", "non_binary"}
VIBES = {"Chill", "Deep talks", "Playful", "Flirty", "Just here to vibe"}

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    age: int = Field(ge=18, le=100)
    gender: str = Field(default="prefer_not_to_say", max_length=32)

    @field_validator("gender")
    @classmethod
    def valid_register_gender(cls, v: str) -> str:
        if v not in GENDERS:
            raise ValueError("Invalid gender")
        return v

class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str

class ProfileRequest(BaseModel):
    bio: str = Field(default="", max_length=2000)
    looking_for: str = Field(default="", max_length=1000)
    interests: list[str] = Field(default_factory=list, max_length=50)
    age: int | None = Field(default=None, ge=18, le=100)
    gender: str = Field(default="prefer_not_to_say", max_length=32)
    country: str = Field(default="", max_length=80)
    state: str = Field(default="", max_length=100)
    city: str = Field(default="", max_length=100)
    district: str = Field(default="", max_length=120)
    vibe: str = Field(default="", max_length=80)
    preferred_gender: str = Field(default="any", max_length=32)
    preferred_country: str = Field(default="any", max_length=80)
    preferred_state: str = Field(default="any", max_length=100)
    min_age: int = Field(default=18, ge=18, le=100)
    max_age: int = Field(default=100, ge=18, le=100)

    @field_validator("gender")
    @classmethod
    def valid_gender(cls, v: str) -> str:
        if v not in GENDERS:
            raise ValueError("Invalid gender")
        return v

    @field_validator("vibe")
    @classmethod
    def valid_vibe(cls, v: str) -> str:
        if v and v not in VIBES:
            raise ValueError("Invalid vibe")
        return v

    @field_validator("preferred_gender")
    @classmethod
    def valid_preferred_gender(cls, v: str) -> str:
        if v not in PREFERRED_GENDERS:
            raise ValueError("Invalid gender preference")
        return v

    @field_validator("max_age")
    @classmethod
    def age_order(cls, v: int, info):
        min_age = info.data.get("min_age", 18)
        if v < min_age:
            raise ValueError("max_age must be greater than or equal to min_age")
        return v

class ConnectionRequest(BaseModel):
    receiver_id: int

class LocationRequest(BaseModel):
    city: str = Field(default="", max_length=100)
    district: str = Field(default="", max_length=120)
    state: str = Field(default="", max_length=100)
    country: str = Field(default="", max_length=80)

class ReportRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=80)
    details: str = Field(default="", max_length=2000)

