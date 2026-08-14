"""
app.py — API server for the Two-Need, Two-Phase Post-Retirement Planner.

This is the ONLY place the calculation logic lives once deployed. The
frontend (app.html) never receives the formulas — it sends a plan as JSON
and receives computed results as JSON.

Run locally:
    pip install flask
    python app.py
    # serves on http://127.0.0.1:5001

Deploy: see DEPLOYMENT.md in this folder for real hosting steps (Render,
Railway, Fly.io, or your own server) — this dev server is NOT meant to be
exposed to the internet as-is.
"""

from flask import Flask, request, jsonify
from engine import compute_plan, goal_seek

app = Flask(__name__)

# Manual CORS handling (no internet access to install flask-cors in this
# environment) — for production, restrict Access-Control-Allow-Origin to
# your actual frontend's domain instead of '*'.
@app.after_request
def add_cors_headers(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
    return resp


@app.route('/api/calculate', methods=['POST', 'OPTIONS'])
def calculate():
    if request.method == 'OPTIONS':
        return '', 204
    try:
        body = request.get_json(force=True)
        plan_data = body.get('plan', body)  # accept either {plan:{...}} or the plan dict directly
        sequence_shock = body.get('sequenceShock')
        result = compute_plan(plan_data, sequence_shock=sequence_shock)
        return jsonify({'ok': True, 'result': result})
    except Exception as e:
        # Deliberately not leaking internals in the message beyond what's
        # needed to debug a malformed request during development.
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.route('/api/goal-seek', methods=['POST', 'OPTIONS'])
def goal_seek_route():
    if request.method == 'OPTIONS':
        return '', 204
    try:
        body = request.get_json(force=True)
        plan_data = body.get('plan')
        variable = body.get('variable')
        target = float(body.get('target', 0))
        result = goal_seek(plan_data, variable, target)
        return jsonify({'ok': True, 'result': result})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'ok': True, 'status': 'healthy'})


if __name__ == '__main__':
    app.run(host="127.0.0.1", port=5001, debug=False)
