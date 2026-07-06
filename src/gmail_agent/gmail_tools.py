from googleapiclient.discovery import build
from gmail_agent.auth import get_gmail_credentials
import base64
import email
from email.mime.text import MIMEText


def get_gmail_service():
    """Создаём подключение к Gmail API"""
    creds = get_gmail_credentials()
    service = build('gmail', 'v1', credentials=creds)
    return service


def read_emails(max_results: int = 5) -> list:
    """
    Читает последние письма из входящих.
    Возвращает список писем с темой, отправителем и текстом.
    """
    service = get_gmail_service()

    # Получаем список последних писем
    results = service.users().messages().list(
        userId='me',
        labelIds=['INBOX'],
        maxResults=max_results
    ).execute()

    messages = results.get('messages', [])
    emails = []

    for msg in messages:
        # Получаем полное письмо по ID
        message = service.users().messages().get(
            userId='me',
            id=msg['id'],
            format='full'
        ).execute()

        headers = message['payload']['headers']

        # Извлекаем заголовки
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Без темы')
        sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Неизвестен')
        date = next((h['value'] for h in headers if h['name'] == 'Date'), '')

        # Извлекаем текст письма
        body = extract_body(message['payload'])

        emails.append({
            'id': msg['id'],
            'subject': subject,
            'from': sender,
            'date': date,
            'body': body[:500]  # Ограничиваем 500 символами
        })

    return emails


def extract_body(payload) -> str:
    """Извлекает текст из тела письма"""
    body = ""

    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain':
                data = part['body'].get('data', '')
                if data:
                    body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                    break
    else:
        data = payload['body'].get('data', '')
        if data:
            body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')

    return body.strip()


def send_email(to: str, subject: str, body: str) -> dict:
    """
    Отправляет письмо.
    to: email получателя
    subject: тема письма
    body: текст письма
    """
    service = get_gmail_service()

    # Создаём письмо
    message = MIMEText(body)
    message['to'] = to
    message['subject'] = subject

    # Кодируем в base64
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    # Отправляем
    result = service.users().messages().send(
        userId='me',
        body={'raw': raw}
    ).execute()

    return {
        'status': 'sent',
        'message_id': result['id'],
        'to': to,
        'subject': subject
    }


def get_email_by_id(email_id: str) -> dict:
    """Получает одно письмо по ID"""
    service = get_gmail_service()

    message = service.users().messages().get(
        userId='me',
        id=email_id,
        format='full'
    ).execute()

    headers = message['payload']['headers']
    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Без темы')
    sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Неизвестен')
    body = extract_body(message['payload'])

    return {
        'id': email_id,
        'subject': subject,
        'from': sender,
        'body': body[:1000]
    }


# Тест
if __name__ == "__main__":
    print("📧 Читаем последние 3 письма...\n")
    emails = read_emails(max_results=3)

    for i, email in enumerate(emails, 1):
        print(f"{'='*50}")
        print(f"📨 Письмо {i}")
        print(f"От: {email['from']}")
        print(f"Тема: {email['subject']}")
        print(f"Дата: {email['date']}")
        print(f"Текст: {email['body'][:200]}...")
        print()