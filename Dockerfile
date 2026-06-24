FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Extra deps not in requirements.txt:
#  - cryptography: PyMySQL needs it for MySQL 8 caching_sha2_password auth
#  - docxtpl: used by the datasheet_gen module to render the CE datasheet
#  - Pillow: downscale large uploaded images so the generated .docx stays small
RUN pip install --no-cache-dir cryptography docxtpl Pillow

COPY . .

# Put /app on the import path so sitecustomize.py (PyMySQL->MySQLdb shim) auto-loads
# at interpreter startup for both `python seed_users.py` and `python app.py`.
ENV PYTHONPATH=/app

EXPOSE 5000

# Wait for MySQL, create the schema once (avoids the reloader's concurrent create_all),
# then start the app. Seed prefilled data separately: docker compose exec web python seed.py
CMD ["sh", "-c", "python wait_for_db.py && python init_db.py && python app.py"]
