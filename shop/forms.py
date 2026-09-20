from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import RepairBooking, Order, ContactMessage, Review, CustomerProfile


class RepairBookingForm(forms.ModelForm):
    class Meta:
        model = RepairBooking
        fields = ['customer_name', 'phone_number', 'device', 'repair_type', 'preferred_time', 'issue_description']
        widgets = {
            'customer_name': forms.TextInput(attrs={'placeholder': 'e.g. Chidinma Okafor'}),
            'phone_number': forms.TextInput(attrs={'placeholder': 'e.g. 0803 123 4567'}),
            'device': forms.TextInput(attrs={'placeholder': 'e.g. Samsung Galaxy A54'}),
            'issue_description': forms.Textarea(attrs={'rows': 4, 'placeholder': 'e.g. Screen cracked after a fall, touch still works on the left side'}),
        }
        labels = {
            'customer_name': 'Your name',
            'phone_number': 'Phone / WhatsApp number',
            'preferred_time': 'Preferred drop-off time',
            'issue_description': 'Describe the issue',
        }


class CheckoutForm(forms.ModelForm):
    # Not a column on Order — this only controls whether the customer's saved
    # profile gets updated afterwards. Ignored for guests, who have no profile
    # to save to; the checkout template hides it for them.
    save_details = forms.BooleanField(
        required=False, initial=True,
        label='Remember these details for next time',
        help_text='Saves your name, phone, email and delivery address to your account.')

    class Meta:
        model = Order
        fields = ['customer_name', 'email', 'phone_number', 'delivery_address', 'payment_method']
        widgets = {
            'customer_name': forms.TextInput(attrs={'placeholder': 'e.g. Chidinma Okafor'}),
            'email': forms.EmailInput(attrs={'placeholder': 'you@example.com'}),
            'phone_number': forms.TextInput(attrs={'placeholder': 'e.g. 0803 123 4567'}),
            'delivery_address': forms.TextInput(attrs={'placeholder': 'e.g. No3 Charismatic Crescent, Ugbokolo'}),
            'payment_method': forms.RadioSelect,
        }
        labels = {
            'customer_name': 'Your name',
            'phone_number': 'Phone / WhatsApp number',
            'delivery_address': 'Delivery address',
            'payment_method': 'How would you like to pay?',
        }


class ContactForm(forms.ModelForm):
    class Meta:
        model = ContactMessage
        fields = ['name', 'email', 'phone_number', 'message']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Your name'}),
            'email': forms.EmailInput(attrs={'placeholder': 'you@example.com'}),
            'phone_number': forms.TextInput(attrs={'placeholder': 'Optional'}),
            'message': forms.Textarea(attrs={'rows': 5, 'placeholder': 'How can we help?'}),
        }
        labels = {'phone_number': 'Phone (optional)'}


class TrackForm(forms.Form):
    reference_number = forms.CharField(label='Order # or repair ticket #', widget=forms.TextInput(attrs={'placeholder': 'e.g. 12 or RBT-1007'}))
    phone_number = forms.CharField(label='Phone number used at checkout / booking', widget=forms.TextInput(attrs={'placeholder': 'e.g. 0803 123 4567'}))


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ['reviewer_name', 'rating', 'title', 'body']
        widgets = {
            'reviewer_name': forms.TextInput(attrs={'placeholder': 'Your name'}),
            'rating': forms.RadioSelect,
            'title': forms.TextInput(attrs={'placeholder': 'Sum it up in a few words'}),
            'body': forms.Textarea(attrs={'rows': 4, 'placeholder': "What did you like or dislike?"}),
        }
        labels = {'reviewer_name': 'Your name', 'title': 'Review title (optional)', 'body': 'Your review'}


class RegisterForm(UserCreationForm):
    """Customer sign-up.

    UserCreationForm already provides the username and the two password
    fields, and runs them through the AUTH_PASSWORD_VALIDATORS configured in
    settings — so the passwords are hashed by Django and never touched by us.
    All that's added here is the handful of details ByteBench needs to get an
    order to someone's door.
    """
    first_name = forms.CharField(
        max_length=150, label='Your name',
        widget=forms.TextInput(attrs={'placeholder': 'e.g. Chidinma Okafor', 'autocomplete': 'name'}))
    email = forms.EmailField(
        label='Email',
        widget=forms.EmailInput(attrs={'placeholder': 'you@example.com', 'autocomplete': 'email'}))
    # Goes to CustomerProfile, not to the User row.
    phone_number = forms.CharField(
        max_length=30, label='Phone / WhatsApp number',
        widget=forms.TextInput(attrs={'placeholder': 'e.g. 0803 123 4567', 'autocomplete': 'tel'}))

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ['username', 'first_name', 'email']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update(
            {'placeholder': 'Pick a username', 'autocomplete': 'username'})
        self.fields['username'].help_text = 'Letters, digits and @/./+/-/_ only.'
        self.fields['password1'].widget.attrs.update({'autocomplete': 'new-password'})
        self.fields['password2'].widget.attrs.update({'autocomplete': 'new-password'})


class CustomerProfileForm(forms.ModelForm):
    """The reusable details a returning customer checks out with."""

    class Meta:
        model = CustomerProfile
        fields = ['default_name', 'default_phone', 'default_email', 'delivery_address']
        widgets = {
            'default_name': forms.TextInput(attrs={'placeholder': 'e.g. Chidinma Okafor', 'autocomplete': 'name'}),
            'default_phone': forms.TextInput(attrs={'placeholder': 'e.g. 0803 123 4567', 'autocomplete': 'tel'}),
            'default_email': forms.EmailInput(attrs={'placeholder': 'you@example.com', 'autocomplete': 'email'}),
            'delivery_address': forms.TextInput(attrs={'placeholder': 'e.g. No3 Charismatic Crescent, Ugbokolo'}),
        }
        labels = {
            'default_name': 'Your name',
            'default_phone': 'Phone / WhatsApp number',
            'default_email': 'Email for receipts',
            'delivery_address': 'Delivery address',
        }
