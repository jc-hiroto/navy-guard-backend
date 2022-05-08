from calendar import week
import queue
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
    main: dict
    pre: dict

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


# get yesterday but skip saturdays.
def get_last_available_day(today: date):
    day = today - timedelta(days=1)
    if day.weekday() == 5:
        day -= timedelta(days=1)
    return day
    
def get_last_day_schedule():
    day = get_last_available_day(date.today())
    day_str = day.strftime("%Y-%m-%d")
    return db.schedules.find_one({"_id": day_str})

def get_latest_schedule():
    return db.schedules.find_one(sort=[("_id", -1)])

def get_members_in_schedule(schedule):
    members = {"main": [], "pre": []}
    for day in schedule["main"]:
        for member in schedule["main"][day]:
            if member["status"] == 1:
                members["main"].append(member["id"])
    for member in schedule["pre"]:
        if member["status"] == 1:
            members["pre"].append(member["id"])
    return members
# Members

@app.get("/api/v1/members")
def get_members():
    members = db.members.find()
    return {"status": "OK", "members": list(members)}

@app.get("/api/v1/members/ignore")
def get_ignored_members():
    ignore = {}
    members = db.members.find({"status": {"$ne": 0}})
    for member in members:
        if str(member["type"]) == "0":
            continue
        if member["type"] not in ignore:
            ignore[member["type"]] = []
        ignore[member["type"]].append(member["_id"])
    return {"status": "OK", "members": ignore}


@app.get("/api/v1/members/latest/{count}")
def get_latest_members(count: int):
    schedule = get_last_day_schedule()
    for pre_member in schedule["pre"]:
        if pre_member["status"] == 0:
            raise HTTPException(status_code=400, detail="Pre-member data integrity error.")
        if pre_member["status"] == -1:
            user_id = int(pre_member["id"])
            break
        else:
            continue
    members = []
    eligible_users = []
    for member in db.members.find({"$or":[{"type":"0"}, {"type": 0}]}):
        eligible_users.append(member["_id"])
    start_idx = eligible_users.index(user_id)        
    while len(members) < count:
        members.append(eligible_users[start_idx])
        start_idx += 1
        if start_idx == len(eligible_users):
            start_idx = 0

    return {"status": "OK", "members": members}

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

@app.get("/api/v1/schedules/history")
def get_schedules_history(start: str, end: str):
    schedules = db.schedules.find({"_id": {"$gte": start, "$lte": end}})
    return {"status": "OK", "schedules": list(schedules)}

def predict_schedule(start_date: date, day_delta: int):
    latest_sche = get_latest_schedule()
    latest_date = date.fromisoformat(latest_sche["_id"])
    if start_date > latest_date:
        # do NOT count satudays
        days_between = 0
        for i in range((start_date - latest_date).days):
            print((latest_date+timedelta(days=i)).isoweekday())
            if (latest_date+timedelta(days=i)).isoweekday() != 6:
                days_between += 1
        day_delta += days_between
    print(day_delta)
    # predict all members from latest to query
    latest_date += timedelta(days=1)
    latest_date += timedelta(days=1) if latest_date.isoweekday() == 6 else timedelta(days=0)
    predicted_schedules = []
    eligible_queues = get_latest_queues().get("members")
    queue_members_in_days = []
    queue_members_cnt = 0
    while len(eligible_queues) > 0:
        day = []
        for key in eligible_queues:
            # check if id is in last list
            if len(queue_members_in_days) > 0 and (key in queue_members_in_days[-1]):
                continue
            day.append(eligible_queues[key].pop()["member_id"])
            queue_members_cnt += 1
        eligible_queues = {k:v for k,v in eligible_queues.items() if v}
        queue_members_in_days.append(day)
    print(queue_members_in_days)
    needed_members_cnt = day_delta * 16 - queue_members_cnt
    print("Needed members:", needed_members_cnt)
    print("Queue members:", queue_members_cnt)
    needed_members = get_latest_members(needed_members_cnt).get("members")
    print(needed_members)
    for i in range(day_delta):
        p_date = latest_date + timedelta(days=i)
        if p_date.isoweekday() == 6:
            continue
        p = {
            "_id": p_date.isoformat(),
            "main":{"1": [], "2": [], "3": [], "4": [], "5": [], "6": [], "7": [], "8": []}
        }
        queue_members = queue_members_in_days[i] if i < len(queue_members_in_days) else []
        start_idx = 4
        while(len(queue_members)) > 0:
            # arrange queue members to the middle (3,4,5,6) of 1 to 8
            if start_idx == 9:
                break
            if len(p["main"][str(start_idx)]) >= 2:
                start_idx += 1
                continue
            p["main"][str(start_idx)].append(queue_members.pop(0))
        start_idx = 1
        while len(needed_members) > 0:
            if start_idx == 9:
                break
            if len(p["main"][str(start_idx)]) >= 2:
                start_idx += 1
                continue
            p["main"][str(start_idx)].append(needed_members.pop(0))
        predicted_schedules.append(p)
    print(predicted_schedules)
    return predicted_schedules

@app.get("/api/v1/schedules/weekPrediction")
def get_schedules_week_prediction():
    # days: sun, mon, tue, wed, thu, fri
    weekday = date.today().weekday() if date.today().isoweekday() != 7 else 0
    left_days_in_week = 5 - weekday
    print("left: ",left_days_in_week)
    if left_days_in_week == 0:
        return {"status": "OK", "schedules": []}
    p_sche = predict_schedule(date.today(), left_days_in_week)
    return {"status": "OK", "schedules": p_sche, "verified": False}

@app.get("/api/v1/schedules/dayPrediction")
def get_schedules_week_prediction():
    p_sche = predict_schedule(date.today(), 1)
    return {"status": "OK", "schedule": p_sche[0] if len(p_sche) > 0 else None, "verified": False}

@app.get("/api/v1/schedules/{schedule_date}")
def get_schedule(schedule_date: str):
    # YYYY-MM-DD
    schedule = db.schedules.find_one({"_id": schedule_date})
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"status": "OK", "schedule": schedule, "verified": True}

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

def organize_queue(queues):
    organized_queues = {}
    for queue in queues:
        if queue["member_id"] not in organized_queues:
            organized_queues[queue["member_id"]] = []
        organized_queues[queue["member_id"]].append(queue)
    return organized_queues

@app.get("/api/v1/queues")
def get_queues():
    queues = list(db.queues.find({"status": 0}))
    organized_queue = organize_queue(queues)
    return {"status": "OK", "queues": list(queues), "members": organized_queue}

@app.get("/api/v1/queues/all")
def get_queues():
    queues = list(db.queues.find())
    organized_queue = organize_queue(queues)
    return {"status": "OK", "queues": list(queues), "members": organized_queue}

@app.get("/api/v1/queues/latest")
def get_latest_queues():
    schedule = get_last_day_schedule()
    queues = list(db.queues.find({"status": 0}))
    organized_queue = organize_queue(queues)
    members = get_members_in_schedule(schedule)
    eligible_queues = {}
    for member_id in organized_queue:
        if member_id in members["main"] or member_id in members["pre"]:
            continue
        else:
            eligible_queues[member_id] = organized_queue[member_id]

    return {"status": "OK", "members": eligible_queues}

    

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