import logging
from typing import Optional, Dict, Any
import aiosmtplib
from email.message import EmailMessage
from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    @staticmethod
    def _render_base_html(title: str, preheader: str, content_html: str) -> str:
        """
        Renders a responsive, modern HTML email template with CleanOnes branding.
        """
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background-color: #f1f5f9;
            color: #1e293b;
            margin: 0;
            padding: 24px 12px;
            -webkit-font-smoothing: antialiased;
        }}
        .wrapper {{
            max-width: 580px;
            margin: 0 auto;
            background: #ffffff;
            border-radius: 12px;
            border: 1px solid #e2e8f0;
            overflow: hidden;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
        }}
        .header {{
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            padding: 28px 32px;
            color: #ffffff;
            text-align: left;
        }}
        .header h1 {{
            margin: 0;
            font-size: 20px;
            font-weight: 700;
            letter-spacing: -0.02em;
            color: #ffffff;
        }}
        .header p {{
            margin: 4px 0 0;
            font-size: 13px;
            color: #94a3b8;
        }}
        .content {{
            padding: 32px;
            font-size: 15px;
            line-height: 1.6;
            color: #334155;
        }}
        .otp-box {{
            background-color: #f8fafc;
            border: 2px dashed #cbd5e1;
            border-radius: 10px;
            padding: 20px;
            text-align: center;
            margin: 24px 0;
        }}
        .otp-code {{
            font-family: 'Courier New', Courier, monospace;
            font-size: 32px;
            font-weight: 800;
            letter-spacing: 6px;
            color: #2563eb;
            margin: 8px 0;
        }}
        .credentials-box {{
            background-color: #f8fafc;
            border-radius: 8px;
            border-left: 4px solid #2563eb;
            padding: 18px 24px;
            margin: 24px 0;
        }}
        .cred-item {{
            margin: 8px 0;
            font-size: 14px;
        }}
        .cred-label {{
            font-weight: 600;
            color: #475569;
            width: 90px;
            display: inline-block;
        }}
        .cred-val {{
            font-family: 'Courier New', Courier, monospace;
            font-size: 15px;
            font-weight: bold;
            color: #0f172a;
        }}
        .alert-box {{
            background-color: #fef2f2;
            border-left: 4px solid #ef4444;
            padding: 14px 18px;
            border-radius: 6px;
            font-size: 13px;
            color: #991b1b;
            margin: 20px 0;
        }}
        .info-box {{
            background-color: #eff6ff;
            border-left: 4px solid #3b82f6;
            padding: 14px 18px;
            border-radius: 6px;
            font-size: 13px;
            color: #1e40af;
            margin: 20px 0;
        }}
        .button {{
            display: inline-block;
            background-color: #2563eb;
            color: #ffffff !important;
            text-decoration: none;
            padding: 12px 28px;
            border-radius: 6px;
            font-weight: 600;
            font-size: 14px;
            margin: 16px 0;
        }}
        .footer {{
            border-top: 1px solid #e2e8f0;
            padding: 20px 32px;
            font-size: 12px;
            color: #94a3b8;
            text-align: center;
            background-color: #fafafa;
        }}
    </style>
</head>
<body>
    <div class="wrapper">
        <div class="header">
            <h1>CleanOnes</h1>
            <p>{preheader}</p>
        </div>
        <div class="content">
            {content_html}
        </div>
        <div class="footer">
            &copy; CleanOnes Professional Cleaning Services. All rights reserved.<br>
            If you did not request this email, you can safely ignore it.
        </div>
    </div>
</body>
</html>"""

    @classmethod
    async def send_email(cls, to_email: str, subject: str, body: str, html_body: Optional[str] = None):
        """
        Sends an email using configured SMTP settings.
        Supports both plain text and optional HTML multipart.
        """
        if not settings.SMTP_HOST or not settings.SMTP_PORT or not settings.SMTP_USER:
            logger.info("SMTP not configured. Fallback logged email to %s: %s | %s", to_email, subject, body)
            return

        message = EmailMessage()
        message["From"] = settings.FROM_EMAIL or settings.SMTP_USER
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)

        if html_body:
            message.add_alternative(html_body, subtype="html")

        try:
            await aiosmtplib.send(
                message,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER,
                password=settings.SMTP_PASSWORD,
                use_tls=True if settings.SMTP_PORT == 465 else False,
                start_tls=True if settings.SMTP_PORT == 587 else False,
                timeout=15.0
            )
            logger.info("Successfully sent SMTP email to %s: %s", to_email, subject)
        except Exception as e:
            logger.error("Failed to send SMTP email to %s: %s", to_email, e)

    @classmethod
    async def send_credentials_email(
        cls,
        to_email: str,
        full_name: str,
        role: str,
        password: str
    ):
        """
        Sends an account creation credentials email with Name, Email, Role, and Password.
        """
        clean_name = full_name.strip() if full_name else "Valued User"
        clean_role = role.strip().capitalize() if role else "User"
        subject = f"Your CleanOnes Account Credentials - {clean_role}"

        plain_text = (
            f"Hello {clean_name},\n\n"
            f"Your account has been created for CleanOnes.\n\n"
            f"Here are your login credentials:\n"
            f"----------------------------------------\n"
            f"Name:     {clean_name}\n"
            f"Email:    {to_email}\n"
            f"Role:     {clean_role}\n"
            f"Password: {password}\n"
            f"----------------------------------------\n\n"
            f"Please log in and update your password upon your first session.\n\n"
            f"Best regards,\n"
            f"The CleanOnes Team"
        )

        content_html = f"""
            <p>Hello <strong>{clean_name}</strong>,</p>
            <p>Welcome to <strong>CleanOnes</strong>! An account has been created for you with the details below:</p>
            
            <div class="credentials-box">
                <div class="cred-item"><span class="cred-label">Name:</span> <span class="cred-val">{clean_name}</span></div>
                <div class="cred-item"><span class="cred-label">Email:</span> <span class="cred-val">{to_email}</span></div>
                <div class="cred-item"><span class="cred-label">Role:</span> <span class="cred-val">{clean_role}</span></div>
                <div class="cred-item"><span class="cred-label">Password:</span> <span class="cred-val">{password}</span></div>
            </div>
            
            <p style="font-size: 13px; color: #64748b;">
                For security reasons, please log in and change your temporary password immediately.
            </p>
        """

        html_body = cls._render_base_html(
            title="CleanOnes Credentials",
            preheader=f"Account Created for {clean_role}",
            content_html=content_html
        )

        await cls.send_email(to_email, subject, plain_text, html_body=html_body)

    @classmethod
    async def send_otp_email(
        cls,
        to_email: str,
        full_name: Optional[str],
        otp: str,
        purpose: str = "verification"
    ):
        """
        Sends a stylish OTP email for Verification, Resend, or Forgot Password flows.
        """
        clean_name = full_name.strip() if full_name else "Valued User"

        if purpose == "forgot_password":
            subject = "CleanOnes - Password Reset OTP Code"
            preheader = "Password Reset Request"
            title = "Password Reset Code"
            intro_text = (
                f"We received a request to reset the password for your CleanOnes account. "
                f"Please use the one-time verification code below to complete the reset process."
            )
            warning_text = (
                "If you did not request a password reset, please ignore this email or contact support immediately. "
                "Never share this verification code with anyone."
            )
        elif purpose == "resend":
            subject = "CleanOnes - Your Verification Code (Resent)"
            preheader = "Email Verification Code"
            title = "Your Verification Code"
            intro_text = f"Here is your requested verification code to complete your CleanOnes email verification:"
            warning_text = "This code will expire in 15 minutes. Please do not share it with anyone."
        else:
            subject = "CleanOnes - Verify Your Email Address"
            preheader = "Welcome to CleanOnes"
            title = "Email Verification"
            intro_text = (
                f"Thank you for registering with CleanOnes! "
                f"Please use the following 6-digit code to verify your email address and activate your account:"
            )
            warning_text = "This verification code will expire in 15 minutes."

        plain_text = (
            f"Hello {clean_name},\n\n"
            f"{intro_text}\n\n"
            f"----------------------------------------\n"
            f"Verification Code: {otp}\n"
            f"Expires In: 15 minutes\n"
            f"----------------------------------------\n\n"
            f"{warning_text}\n\n"
            f"Best regards,\n"
            f"The CleanOnes Team"
        )

        content_html = f"""
            <p>Hello <strong>{clean_name}</strong>,</p>
            <p>{intro_text}</p>
            
            <div class="otp-box">
                <div style="font-size: 12px; font-weight: 600; text-transform: uppercase; color: #64748b; letter-spacing: 1px;">One-Time Verification Code</div>
                <div class="otp-code">{otp}</div>
                <div style="font-size: 13px; color: #64748b;">Valid for <strong>15 minutes</strong></div>
            </div>
            
            <div class="info-box">
                {warning_text}
            </div>
        """

        html_body = cls._render_base_html(
            title=title,
            preheader=preheader,
            content_html=content_html
        )

        await cls.send_email(to_email, subject, plain_text, html_body=html_body)

    @classmethod
    async def send_password_changed_email(
        cls,
        to_email: str,
        full_name: Optional[str]
    ):
        """
        Sends a security confirmation email after password reset or password change.
        """
        clean_name = full_name.strip() if full_name else "Valued User"
        subject = "CleanOnes - Password Successfully Updated"

        plain_text = (
            f"Hello {clean_name},\n\n"
            f"Your CleanOnes account password was recently updated.\n\n"
            f"If you performed this action, no further steps are needed.\n"
            f"If you did not initiate this change, please contact support immediately to secure your account.\n\n"
            f"Best regards,\n"
            f"The CleanOnes Team"
        )

        content_html = f"""
            <p>Hello <strong>{clean_name}</strong>,</p>
            <p>This is a confirmation that your <strong>CleanOnes</strong> account password has been successfully updated.</p>
            
            <div class="info-box">
                <strong>Security Notice:</strong> If you made this change, you can safely disregard this email.
            </div>
            
            <div class="alert-box">
                <strong>Didn't make this change?</strong> Please contact CleanOnes Administrator or Support immediately.
            </div>
        """

        html_body = cls._render_base_html(
            title="Password Updated",
            preheader="Account Security Notification",
            content_html=content_html
        )

        await cls.send_email(to_email, subject, plain_text, html_body=html_body)

    @classmethod
    async def send_account_status_email(
        cls,
        to_email: str,
        full_name: Optional[str],
        status_text: str,
        role: str
    ):
        """
        Sends an account approval or status change notification email.
        """
        clean_name = full_name.strip() if full_name else "Valued User"
        clean_role = role.strip().capitalize() if role else "User"
        is_approved = status_text.lower() in ["approved", "active"]

        subject = f"CleanOnes - Account {status_text.capitalize()}"

        plain_text = (
            f"Hello {clean_name},\n\n"
            f"Your CleanOnes {clean_role} account has been {status_text}.\n\n"
            f"You can now log in to the CleanOnes platform to access your dashboard.\n\n"
            f"Best regards,\n"
            f"The CleanOnes Team"
        )

        content_html = f"""
            <p>Hello <strong>{clean_name}</strong>,</p>
            <p>Great news! Your <strong>CleanOnes {clean_role}</strong> account has been <strong>{status_text}</strong> by the management team.</p>
            
            <div class="{'info-box' if is_approved else 'alert-box'}">
                Account Status: <strong>{status_text.capitalize()}</strong>
            </div>
            
            <p>You can now log into your mobile or web portal using your registered email.</p>
        """

        html_body = cls._render_base_html(
            title="Account Status Update",
            preheader=f"Account Status: {status_text.capitalize()}",
            content_html=content_html
        )

        await cls.send_email(to_email, subject, plain_text, html_body=html_body)

    @classmethod
    async def send_report_summary_email(
        cls,
        to_email: str,
        company_name: str,
        report_title: str,
        report_details: Optional[str] = None
    ):
        """
        Sends a service report summary email to a client.
        """
        subject = f"CleanOnes - Service Report: {report_title}"

        plain_text = (
            f"Hello,\n\n"
            f"Please find below the cleaning and service summary report for {company_name}:\n\n"
            f"Report: {report_title}\n"
            f"{report_details or 'All scheduled cleaning visits were inspected and completed according to quality standards.'}\n\n"
            f"Best regards,\n"
            f"The CleanOnes Team"
        )

        content_html = f"""
            <p>Hello,</p>
            <p>Here is the latest service and inspection report for <strong>{company_name}</strong>:</p>
            
            <div class="credentials-box">
                <div class="cred-item"><strong>Report:</strong> {report_title}</div>
                <div class="cred-item"><strong>Client:</strong> {company_name}</div>
                <p style="margin-top: 12px; font-size: 14px; color: #475569;">
                    {report_details or 'All scheduled cleaning visits and room tasks were completed according to quality assurance guidelines.'}
                </p>
            </div>
        """

        html_body = cls._render_base_html(
            title="Service Report",
            preheader=f"Report for {company_name}",
            content_html=content_html
        )

        await cls.send_email(to_email, subject, plain_text, html_body=html_body)
