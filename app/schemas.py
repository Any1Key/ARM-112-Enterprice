from typing import Literal
from pydantic import BaseModel, Field

class CardIn(BaseModel):
    incident_type: str = Field(default='',max_length=3000)
    classifier_id: int | None = None
    classifier_ids: list[int] = Field(default_factory=list,max_length=10)
    address: str = Field(default='',max_length=1000)
    description: str = Field(default='',max_length=10000)
    services: list[str] = Field(default_factory=list,max_length=100)
    operator_comment: str = Field(default='',max_length=5000)
    caller_name: str = Field(default='',max_length=200)
    caller_phone: str = Field(default='',max_length=100)
    aon: str = Field(default='',max_length=100)
    on_site_phone: str = Field(default='',max_length=100)
    caller_status: str = Field(default='',max_length=100)
    country: str = Field(default='Россия',max_length=100)
    region: str = Field(default='',max_length=100)
    city: str = Field(default='',max_length=100)
    district: str = Field(default='',max_length=100)
    borough: str = Field(default='',max_length=100)
    street: str = Field(default='',max_length=200)
    house: str = Field(default='',max_length=50)
    building: str = Field(default='',max_length=50)
    structure: str = Field(default='',max_length=50)
    apartment: str = Field(default='',max_length=50)
    entrance: str = Field(default='',max_length=50)
    floor: str = Field(default='',max_length=50)
    access_code: str = Field(default='',max_length=50)
    descriptive_address: str = Field(default='',max_length=1000)
    latitude: float | None = Field(default=None,ge=-90,le=90)
    longitude: float | None = Field(default=None,ge=-180,le=180)
    source_system: str = Field(default='Служба 112',max_length=100)
    vis_class: str = Field(default='',max_length=300)
    channel: str = Field(default='Учебный текстовый вызов',max_length=100)
    object_name: str = Field(default='',max_length=300)
    no_contact: bool = False
    call_lost: bool = False
    emergency: bool = False
    incident_alert: bool = False
    important: bool = False
    victims: bool = False
    access_blocked: bool = False
    life_danger: bool = False
    flags: dict[str,bool] = Field(default_factory=dict,max_length=30)
    questionnaire: str = Field(default='',max_length=5000)

class DraftIn(BaseModel):
    request_id: str | None = Field(default=None,max_length=64)
    card: CardIn
    revision: int = Field(ge=0)

class ResolveIn(BaseModel):
    classifier_ids: list[int] = Field(default_factory=list,max_length=10)
    flags: dict[str,bool] = Field(default_factory=dict,max_length=30)

class SettingsIn(BaseModel):
    generation_job_id:int|None=None
    difficulty: Literal['basic','advanced','complex'] = 'basic'
    mode: Literal['call','dispatch'] = 'call'
    initial_card: CardIn = Field(default_factory=CardIn)
    published: bool = False

class LessonIn(BaseModel):
    title: str = Field(min_length=1,max_length=300)
    mode: Literal['call','dispatch'] = 'call'
    scenario_ids: list[int] = Field(min_length=1,max_length=100)
    student_ids: list[int] = Field(min_length=1,max_length=100)
    service_code: str = Field(default='TERRITORY',min_length=1,max_length=100)

class StatusIn(BaseModel):
    service_code: str = Field(min_length=1,max_length=100)
    status: str = Field(min_length=1,max_length=100)
    comment: str = Field(default='',max_length=5000)
    unit_number: str = Field(default='',max_length=100)

class WorkCallIn(BaseModel):
    service: str = Field(min_length=1,max_length=200)
    destination: str = Field(default='',max_length=300)
    phone: str = Field(default='',max_length=100)
    receiver: str = Field(default='',max_length=200)
    message: str = Field(min_length=1,max_length=3000)

class ReviewIn(BaseModel):
    score: int = Field(ge=0,le=100)
    comment: str = Field(min_length=1,max_length=5000)

class UserIn(BaseModel):
    username: str = Field(min_length=3,max_length=64,pattern=r'^[a-zA-Z0-9_.-]+$')
    password: str = Field(min_length=10,max_length=72)
    role: Literal['admin','teacher','student']

class UserUpdateIn(BaseModel):
    role: Literal['admin','teacher','student']
    blocked: bool = False
