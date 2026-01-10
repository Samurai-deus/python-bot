from datetime import datetime

def is_good_time():
    hour = datetime.utcnow().hour
    return 7 <= hour <= 20
