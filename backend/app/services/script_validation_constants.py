"""Shared constants for generated customer-facing script validation."""

TELUGU_BANNED_STEMS = (
    "హాజరు", "ఆహ్వాన", "వివాహ", "పెళ్లి", "వేడుక", "కార్యక్రమ", "ధన్యవాద",
    "శుభాకాంక్ష", "ప్రశ్న", "సందేహ", "సమయం", "తేదీ", "వివరాల", "ధృవీకరించ",
    "నిర్ధారించ", "అందుబాటులో", "సౌకర్య", "కుటుంబ", "దయచేసి", "సంప్రదించ",
    "విజ్ఞప్తి", "ఆశీర్వదించ", "స్థలం", "ప్రదేశం", "తెలియజేయ",
)

TELUGU_MONTH_STEMS = ("జనవరి", "ఫిబ్రవరి", "మార్చి", "ఏప్రిల్", "మే", "జూన్", "జూలై", "ఆగస్టు", "సెప్టెంబర్", "అక్టోబర్", "నవంబర్", "డిసెంబర్")
HINDI_MONTH_STEMS = ("जनवरी", "फरवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर")

ALLOWED_COMMON_ENGLISH = {
    "a", "an", "the", "i", "am", "is", "are", "you", "your", "from", "for", "to", "and", "on", "in", "hello", "january", "okay", "any", "have",
    "my", "our", "their", "this", "that", "call", "name", "date", "time", "details", "confirm", "available",
    "convenient", "please", "thank", "thanks", "sorry", "wishes", "question", "doubt", "contact", "request",
    "place", "venue", "call", "message", "number", "free", "busy", "problem", "support", "vote", "appointment",
    "meeting", "update", "offer", "service", "address", "location", "wedding", "invite", "invitation", "attend",
    "function", "event", "family", "everyone", "morning", "evening", "party", "assistant", "ai", "robot",
}
