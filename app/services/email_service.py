import aiosmtplib
from email.message import EmailMessage
from app.core.config import settings

class EmailService:
    @staticmethod
    async def send_email(to_email: str, subject: str, body: str):
        if not settings.SMTP_HOST or not settings.SMTP_PORT or not settings.SMTP_USER:
            print(f"Mock Email to {to_email}: {subject} | {body}")
            return
            
        message = EmailMessage()
        message["From"] = settings.FROM_EMAIL or settings.SMTP_USER
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)

        try:
            await aiosmtplib.send(
                message,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER,
                password=settings.SMTP_PASSWORD,
                use_tls=True if settings.SMTP_PORT == 465 else False,
                start_tls=True if settings.SMTP_PORT == 587 else False,
            )
        except Exception as e:
            print(f"Failed to send email: {e}")
