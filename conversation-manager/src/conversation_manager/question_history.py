"""Small, redacted presentation records derived from confirmed input replies."""
def summary(record, response):
    if record.get('kind') != 'user_input':
        return None
    questions = ((record.get('native') or {}).get('params') or {}).get('questions', [])
    answers = (response.get('native_result') or {}).get('answers', {})
    if not questions or not isinstance(answers, dict):
        return None
    lines = []
    for question in questions:
        value = answers.get(question['id'], '')
        if isinstance(value, dict):
            value = ', '.join(value.get('answers', []))
        lines.append(question.get('question', '') + '\n' + ('[Private answer]' if question.get('isSecret') else str(value)))
    return dict(request_id=record['request_id'], text='\n\n'.join(lines))
