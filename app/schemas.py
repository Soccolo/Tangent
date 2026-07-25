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


class ActivityIn(BaseModel):
    text: str = Field(min_length=2, max_length=2000)
    source: str = Field(default="manual", max_length=32)


class ChoosePick(BaseModel):
    index: int = Field(ge=0, le=20)


class AnswerSet(BaseModel):
    answers: list[int]
