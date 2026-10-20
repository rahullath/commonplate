"""Everything that identifies this business, in one place.

Defaults describe a fictional sample kitchen so the app runs out of the box;
set the BIZ_* variables to describe yours. Each value can be set with an environment variable (BIZ_*) so a
deployment never needs a code edit. Templates read these as `biz.<name>`;
Python modules import `business` directly.

Phone numbers are stored once, in E.164 form, and every display variant
(spaced, local, digits-only) is derived from that single value.
"""
import os


def _env(name: str, default: str) -> str:
    return os.environ.get(f"BIZ_{name}", default)


def _spaced(e164: str) -> str:
    """'+919876543210' -> '+91 98765 43210' (Indian mobile grouping)."""
    d = e164.lstrip("+")
    if d.startswith("91") and len(d) == 12:
        return f"+91 {d[2:7]} {d[7:]}"
    return e164


class Business:
    name = _env("NAME", "Sample Kitchen")
    instagram = _env("INSTAGRAM", "")
    email = _env("EMAIL", "hello@example.com")
    site_url = _env("SITE_URL", "https://example.com").rstrip("/")
    service_areas = [a.strip() for a in _env("SERVICE_AREAS", "").split(",") if a.strip()]
    google_site_verification = _env("GOOGLE_SITE_VERIFICATION", "")
    bing_site_verification = _env("BING_SITE_VERIFICATION", "")
    facebook_domain_verification = _env("FACEBOOK_DOMAIN_VERIFICATION", "")
    ga_measurement_id = _env("GA_MEASUREMENT_ID", "")
    tagline = _env("TAGLINE", "Home-style food, cooked to order")
    about = _env("ABOUT", "Tell customers who you are and how you cook. Set BIZ_ABOUT, or edit app/templates/about.html.")

    # Address parts
    street_no = _env("STREET_NO", "12")
    street = _env("STREET", "Market Road")
    locality = _env("LOCALITY", "Old Town")
    city = _env("CITY", "Sampletown")
    pincode = _env("PINCODE", "000000")
    state = _env("STATE", "Sample State")
    country = _env("COUNTRY", "India")

    # Kitchen phone (calls and WhatsApp) and a second WhatsApp contact
    phone_e164 = _env("PHONE", "+919876543210")
    contact_whatsapp_e164 = _env("CONTACT_WHATSAPP", "+919876543211")

    # Opening hours: (open, close) as shown to customers
    hours_mon_sat = (_env("HOURS_MON_SAT_OPEN", "9 AM"), _env("HOURS_MON_SAT_CLOSE", "9 PM"))
    hours_sunday = (_env("HOURS_SUN_OPEN", "11 AM"), _env("HOURS_SUN_CLOSE", "9 PM"))

    # ---- derived: phone
    @property
    def phone_display(self) -> str:
        return _spaced(self.phone_e164)

    @property
    def phone_digits(self) -> str:
        return self.phone_e164.lstrip("+")

    @property
    def phone_local(self) -> str:
        """Without the country code: '98765 43210'."""
        return self.phone_display.split(" ", 1)[1] if self.phone_display.startswith("+91 ") else self.phone_e164

    @property
    def phone_ten(self) -> str:
        """Ten digits, no spaces: '9876543210'."""
        return self.phone_digits[-10:]

    @property
    def contact_whatsapp_display(self) -> str:
        return _spaced(self.contact_whatsapp_e164)

    @property
    def contact_whatsapp_digits(self) -> str:
        return self.contact_whatsapp_e164.lstrip("+")

    # ---- derived: address
    @property
    def pickup_line(self) -> str:
        """'12 Market Road, Old Town'"""
        return f"{self.street_no} {self.street}, {self.locality}"

    @property
    def address(self) -> str:
        """'12 Market Road, Old Town, Sampletown 000000'"""
        return f"{self.pickup_line}, {self.city} {self.pincode}"

    @property
    def address_commas(self) -> str:
        """'12, Market Road, Old Town, Sampletown 000000'"""
        return f"{self.street_no}, {self.street}, {self.locality}, {self.city} {self.pincode}"

    @property
    def address_commas_short(self) -> str:
        """'12, Market Road, Old Town, Sampletown'"""
        return f"{self.street_no}, {self.street}, {self.locality}, {self.city}"

    @property
    def street_address(self) -> str:
        """'12, Market Road, Old Town' (schema.org streetAddress)"""
        return f"{self.street_no}, {self.street}, {self.locality}"

    @property
    def address_full(self) -> str:
        """Courier form: '12, Market Road, Old Town, Sampletown, 000000, Sample State, India'"""
        return f"{self.street_address}, {self.city}, {self.pincode}, {self.state}, {self.country}"

    # ---- derived: hours
    @property
    def hours(self) -> str:
        """Compact: 'Mon–Sat 9 AM–9 PM, Sun 11 AM–9 PM'"""
        return f"Mon–Sat {self.hours_mon_sat[0]}–{self.hours_mon_sat[1]}, Sun {self.hours_sunday[0]}–{self.hours_sunday[1]}"

    @property
    def hours_spaced(self) -> str:
        """Spaced: 'Mon–Sat 9 AM – 9 PM, Sun 11 AM – 9 PM'"""
        return f"Mon–Sat {self.hours_mon_sat[0]} – {self.hours_mon_sat[1]}, Sun {self.hours_sunday[0]} – {self.hours_sunday[1]}"


business = Business()
