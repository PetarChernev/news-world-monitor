# Start with lightweight Python
FROM python:3.13-slim
ENV GCP_PROJECT=world-monitor
# # Create a non-root user
# RUN useradd --create-home appuser
# USER appuser

# # Set working dir for the new user
# WORKDIR /home/appuser/app
# COPY --chown=appuser:appuser . .

# Set working dir
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt
# Copy code
COPY . .


# Run FastAPI with Uvicorn
CMD ["python", "-m", "uvicorn", "ai_api:app", "--host", "0.0.0.0", "--port", "8080"]
