# Setting up a Pyton Script as a Windows Service
 

## Install pywin32:
```bash
pip install pywin32
```

## Install the service:
```python
python Service_Wrapper.py install
```

## (Optional) Install under a specific user:
```python
python Service_Wrapper.py --username ".\\SomeUser" --password "SomePassword" install
```

## Start the service:
```python
python Service_Wrapper.py start
```

## Stop the service:
```python
python Service_Wrapper.py stop
```
## Force Stop of service:
```bash
taskkill /F /FI "SERVICES eq TestUploaderService"
```

## Delete the service:
```python
python Service_Wrapper.py remove
```

## Simple UI monitor (Local REST API + Windows App)
Run the local API server:
```python
python service_api.py
```

Run the Windows UI app:
```python
python service_ui.py
```

Optional environment variables:
- `SERVICE_NAME` (default: TestUploaderService)
- `SERVICE_API_HOST` (default: 127.0.0.1)
- `SERVICE_API_PORT` (default: 8085)