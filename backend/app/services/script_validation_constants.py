"""Shared constants for generated customer-facing script validation."""

TELUGU_BANNED_STEMS = (
    "హాజరు", "ఆహ్వాన", "వివాహ", "పెళ్లి", "వేడుక", "కార్యక్రమ", "ధన్యవాద",
    "శుభాకాంక్ష", "ప్రశ్న", "సందేహ", "సమయం", "తేదీ", "వివరాల", "ధృవీకరించ",
    "నిర్ధారించ", "అందుబాటులో", "సౌకర్య", "కుటుంబ", "దయచేసి", "సంప్రదించ",
    "విజ్ఞప్తి", "ఆశీర్వదించ", "స్థలం", "ప్రదేశం", "తెలియజేయ",
)

# These are not invalid Telugu in isolation.  They are the recurring
# over-translated defaults that make a phone script sound formal and unlike a
# real Telugu-English conversation.  Enforce them only for newly generated
# scripts; existing owner-authored scripts remain publishable.
TELUGU_OVER_TRANSLATED_STEMS = (
    "\u0c28\u0c2e\u0c38\u0c4d\u0c15\u0c3e\u0c30\u0c02",  # namaskaram
    "\u0c17\u0c41\u0c30\u0c3f\u0c02\u0c1a\u0c3f",          # gurinchi
    "\u0c1a\u0c46\u0c2a\u0c4d\u0c2a",                      # cheppa...
    "\u0c05\u0c30\u0c4d\u0c25\u0c2e",                      # artham...
    "\u0c24\u0c2a\u0c4d\u0c2a\u0c15\u0c41\u0c02\u0c21\u0c3e",  # tappakunda
    "\u0c2e\u0c3e\u0c24\u0c4d\u0c30\u0c2e\u0c47",          # matrame
    "\u0c2e\u0c30\u0c3f\u0c2f\u0c41",                      # mariyu
    "\u0c30\u0c4b\u0c1c\u0c41 \u0c2c\u0c3e\u0c17\u0c41\u0c02\u0c21",  # roju bagund...
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
