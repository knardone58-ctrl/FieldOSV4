from streamlit.testing.v1 import AppTest
from pathlib import Path

app = AppTest.from_file('app.py')
app.run(timeout=30)
for key in sorted(app.session_state.keys()):
    print(key, '->', app.session_state[key])
