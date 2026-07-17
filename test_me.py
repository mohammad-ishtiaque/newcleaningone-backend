import urllib.request
import json
import traceback

def test():
    try:
        # Login
        req = urllib.request.Request(
            'http://localhost:8080/auth/login',
            data=b'{"email":"admin@cleaning.com", "password":"Admin123"}',
            headers={'Content-Type': 'application/json'}
        )
        res = urllib.request.urlopen(req)
        token_data = json.loads(res.read())
        token = token_data['access_token']
        print(f"Token acquired: {token[:10]}...")

        # Get me
        req2 = urllib.request.Request(
            'http://localhost:8080/auth/me',
            headers={'Authorization': 'Bearer ' + token}
        )
        res2 = urllib.request.urlopen(req2)
        me_data = json.loads(res2.read())
        print(f"Me data: {me_data}")
        print("SUCCESS")
    except urllib.error.HTTPError as e:
        print(f"HTTPError: {e.code} - {e.read().decode()}")
    except Exception as e:
        traceback.print_exc()

if __name__ == "__main__":
    test()
