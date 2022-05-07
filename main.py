from sqlite3 import Date
from jose import JWTError
import uvicorn
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from datetime import date, datetime, time
import uuid
from pydantic import BaseModel, Field
from authorization import *
from pymongo import MongoClient

host_url = '0.0.0.0'
port = 5000
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Req types
class MemberPatch(BaseModel):
    type: str

class Schedule(BaseModel):
    id: str = Field(alias='_id')
    data: dict

class QueuePost(BaseModel):
    member_id: int
    skipped_date: date
    status: int

class QueuePatch(BaseModel):
    status: int

class UserCreate(BaseModel):
    id: str = Field(alias='_id')
    name: str
    type: int
    password: str

class TokenRequestForm(BaseModel):
    id: str = Field(alias='_id')
    password: str

# MongoDB
client = MongoClient(os.environ["MONGO_URL"])
db = client["navy-guard"]
print(db.list_collection_names())

# Healthcheck

@app.get("/api/v1/healthcheck")
def healthcheck():
    return {"status": "OK"}


# Members

@app.get("/api/v1/members")
def get_members():
    members = db.members.find()
    return {"status": "OK", "members": list(members)}

@app.get("/api/v1/members/{member_id}")
def get_member(member_id: int):
    member = db.members.find_one({"_id": member_id})
    print(member)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return {"status": "OK", "member": member}


@app.get("/api/v1/members/{member_id}/queue")
def get_member(member_id: int):
    if db.members.find_one({"_id": member_id}) is None:
        raise HTTPException(status_code=404, detail="Member not found")
    queue = db.queues.find({"member_id": member_id})
    return {"status": "OK", "queue": list(queue)}
 
@app.patch("/api/v1/members/{member_id}")
def update_member(member_id: int, member: MemberPatch, token: str = Depends(oauth2_scheme)):
    db.members.update_one({"_id": member_id}, {"$set": member.dict(by_alias=True)})
    return {"status": "OK"}

# Schedules

@app.get("/api/v1/schedules")
def get_schedules():
    schedules = db.schedules.find()
    return {"status": "OK", "schedules": list(schedules)}

@app.get("/api/v1/schedules/{schedule_date}")
def get_schedule(schedule_date: str):
    # YYYY-MM-DD
    schedule = db.schedules.find_one({"_id": schedule_date})
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"status": "OK", "schedule": schedule}

@app.post("/api/v1/schedules")
def create_schedule(schedule: Schedule, token: str = Depends(oauth2_scheme)):
    print(schedule)
    db.schedules.insert_one(schedule.dict(by_alias=True))
    return {"status": "OK"}

@app.patch("/api/v1/schedules/{schedule_date}")
def update_schedule(schedule_date: str, schedule: Schedule, token: str = Depends(oauth2_scheme)):
    db.schedules.update_one({"_id": schedule_date}, {"$set": schedule.dict(by_alias=True)})
    return {"status": "OK"}

@app.delete("/api/v1/schedules/{schedule_date}")
def delete_schedule(schedule_date: str, token: str = Depends(oauth2_scheme)):
    db.schedules.delete_one({"_id": schedule_date})
    return {"status": "OK"}


# Queues

@app.get("/api/v1/queues")
def get_queues():
    queues = db.queues.find()
    return {"status": "OK", "queues": list(queues)}

@app.get("/api/v1/queues/{queue_id}")
def get_queue(queue_id: int):
    queue = db.queues.find_one({"_id": queue_id})
    if queue is None:
        raise HTTPException(status_code=404, detail="Queue not found")
    return {"status": "OK", "queue": queue}

@app.post("/api/v1/queues")
def create_queue(queue: QueuePost, token: str = Depends(oauth2_scheme)):
    dict_queue = queue.dict(by_alias=True)
    dict_queue["skipped_date"] = datetime.combine(dict_queue["skipped_date"], time.min)
    dict_queue["_id"] = str(uuid.uuid4())
    db.queues.insert_one(dict_queue)
    return {"status": "OK"}

@app.patch("/api/v1/queues/{queue_id}")
def update_queue(queue_id: str, queue: QueuePatch, token: str = Depends(oauth2_scheme)):
    db.queues.update_one({"_id": queue_id}, {"$set": queue.dict(by_alias=True)})
    return {"status": "OK"}

@app.delete("/api/v1/queues/{queue_id}")
def delete_queue(queue_id: str, token: str = Depends(oauth2_scheme)):
    db.queues.delete_one({"_id": queue_id})
    return {"status": "OK"}

# USERS
def authenticate_user(_id: str, password: str):
    user = db.users.find_one({"_id": _id})
    if not user:
        return False
    print(user)
    if not verify_password(password, user["password"]):
        return False
    return user

def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        id: str = payload.get("sub")
        if id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.users.find_one({"_id": id})
    if user is None:
        raise credentials_exception
    return user


@app.post(f"/api/v1/auth")
def sign_in_for_access_token(form_data: TokenRequestForm):
    user = authenticate_user(form_data.id, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    print(user["_id"])
    access_token = create_access_token(
        data={"sub": str(user["_id"])}, expires_delta=access_token_expires)
    return {
        "status":0,
        "message": "Login Successful. Access Token Generated.",
        "data": {
            "_id": user["_id"],
            "token": access_token,
            "token_type": "Bearer"
        }
    }

@app.post(f"/api/v1/users/")
def create_user(user: UserCreate, token: str = Depends(oauth2_scheme)):
    creator = check_sudo_permission(token)
    db_user = db.users.find_one({"_id": user.id})
    if db_user:
        raise HTTPException(status_code=400, detail="id already registered")
    user.password = get_password_hash(user.password)
    db.users.insert_one(user.dict(by_alias=True))
    return {
        "status":0,
        "message": "User created successfully.",
    }

def check_sudo_permission(token):
    user = get_current_user(db=db, token=token)
    if str(["type"]) != "2":
        raise HTTPException(status_code=403, detail="Forbidden. Pemission Error.")
    return user

if __name__ == "__main__":
    uvicorn.run(app='main:app', host=host_url, port=port, reload=True)