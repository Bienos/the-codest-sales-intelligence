-- When a person is erased (right to be forgotten), every conversation row about them goes too, whichever path erased them.
CREATE OR REPLACE FUNCTION erase_person_conversations() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.erased_at IS NOT NULL AND OLD.erased_at IS NULL THEN
    DELETE FROM contact_interactions WHERE person_id = NEW.id;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS people_erase_conversations ON people;
CREATE TRIGGER people_erase_conversations AFTER UPDATE OF erased_at ON people FOR EACH ROW EXECUTE FUNCTION erase_person_conversations();
