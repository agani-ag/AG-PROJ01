from django.core.validators import RegexValidator

# =============== Validators ===============
phone_validator = RegexValidator(
    regex=r'^\+?\d{7,15}$',
    message="Phone number must be entered in the format: '+999999999'. Up to 15 digits allowed."
)

pincode_validator = RegexValidator(
    regex=r'^\d{4,10}$',
    message="Pincode must be between 4 and 10 digits."
)