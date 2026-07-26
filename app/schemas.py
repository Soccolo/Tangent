from pydantic import BaseModel, Field


class SignUp(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(default="", max_length=120)
    role: str = Field(default="", max_length=2000)


class SignIn(BaseModel):
    email: str
    password: str


class ProfileUpdate(BaseModel):
    display_name: str = Field(default="", max_length=120)
    role: str = Field(default="", max_length=2000)
    bio: str = Field(default="", max_length=280)
    accent: str = Field(default="", max_length=16)
    # A resized data: URL, not a file — see models.User.avatar. Generous ceiling
    # because base64 inflates ~33%; the client resizes to 256px before sending.
    avatar: str | None = Field(default=None, max_length=400_000)
    contribute_to_library: bool | None = None
    theme: str | None = Field(default=None, max_length=16)
    default_level: int | None = Field(default=None, ge=1, le=10)


class ForgotPassword(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class ResetPassword(BaseModel):
    token: str = Field(min_length=8, max_length=64)
    password: str = Field(min_length=8, max_length=200)


class AccountDelete(BaseModel):
    # Requires the current password: deletion is irreversible, and a stolen
    # session shouldn't be enough to wipe someone's account.
    password: str = Field(min_length=1, max_length=200)


class ActivityIn(BaseModel):
    text: str = Field(min_length=2, max_length=2000)
    source: str = Field(default="manual", max_length=32)


class PlacementResult(BaseModel):
    probes: str = Field(default="", max_length=500)
    prompt: str = Field(default="", max_length=1000)
    correct: bool


class ChoosePick(BaseModel):
    index: int = Field(ge=0, le=20)
    level: int = Field(default=5, ge=1, le=10)
    # Optional: the two diagnostic questions are a shortcut to a better-pitched
    # lesson, not a gate. Skipping them just means we trust the self-rating.
    placement: list[PlacementResult] = Field(default_factory=list, max_length=4)


class PlacementAsk(BaseModel):
    index: int = Field(ge=0, le=20)


class AnswerSet(BaseModel):
    answers: list[int]
