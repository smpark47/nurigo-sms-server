from flask import Flask, request, jsonify
import requests
import base64

app = Flask(__name__)

API_KEY = "NCSQ4IUXA7HZXKZP"
API_SECRET = "Z32QAUC937DLGU82U92OUGUY75ZAIAGI"
FROM_NUMBER = "01000000000"  # 실제 발신번호로 교체 필요

headers = {
    "Authorization": "Basic " + base64.b64encode(f"{API_KEY}:{API_SECRET}".encode()).decode(),
    "Content-Type": "application/json"
}

@app.route("/send-bulk", methods=["POST"])
def send_bulk():
    data = request.json
    results = []
    for student in data:
        text = generate_message(student)
        payload = {
            "message": {
                "to": student.get("phone", ""),
                "from": FROM_NUMBER,
                "text": text
            }
        }
        r = requests.post("https://api.solapi.com/messages/v4/send", json=payload, headers=headers)
        results.append({"name": student.get("name"), "status": r.status_code, "result": r.json()})
    return jsonify(results)

def generate_message(student):
    return f"""안녕하세요. 서울더함수학학원입니다.
<월간보고>
성명: {student.get('name')}

진도: {student.get('grade')} {student.get('level')} {student.get('subject')} {student.get('chapter')} {student.get('subchapter')}

1. 수업 태도
집중력: {student.get('focus')}/10
진도 소화도: {student.get('progress')}/10
성실도: {student.get('diligence')}/10

2. 과제 수행
양적 소화도: {student.get('quantity')}/10
질적 소화도: {student.get('quality')}/10
결과 및 점수: {student.get('score')}/10

3. 성취도
기본 난도: {student.get('basic')}/10
중간 난도: {student.get('intermediate')}/10
심화 난도: {student.get('advanced')}/10

4. 특이사항
{student.get('specialNotes')}
"""

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
